"""
FastAPI application entry point.

This module configures and creates the FastAPI application with:
- CORS middleware
- Request timing middleware
- Global exception handling
- API route registration
- Background scheduler initialization
"""

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from app.api import api_router
from app.api.admin.ui import admin_ui_auth_redirect_response
from app.core.auth import require_admin_key
from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.logging import get_logger, setup_logging
from app.db.base import Base, SessionLocal, engine
from app.scheduler.tasks import fetch_and_process_news

# Configure logging first
setup_logging()
logger = get_logger(__name__)


SCHEDULER_LOCK_KEY = os.getenv("SCHEDULER_LEADER_LOCK_KEY", "scheduler_lock")
SCHEDULER_LOCK_TTL_SECONDS = int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "120"))
SCHEDULER_LOCK_REFRESH_SECONDS = int(os.getenv("SCHEDULER_LOCK_REFRESH_SECONDS", "30"))

_scheduler_lock_stop_event: Optional[threading.Event] = None
_scheduler_lock_token: Optional[str] = None
_scheduler_lock_redis = None


def _create_tables() -> None:
    """Create database tables if they don't exist.

    In production (Render), we skip this because Alembic migrations handle schema.
    In development, we create tables automatically for convenience.
    """
    # Skip in production - Alembic handles migrations
    if os.getenv("RENDER") or os.getenv("SKIP_CREATE_TABLES", "").lower() == "true":
        logger.info("Skipping auto table creation (production mode - use Alembic migrations)")
        return

    try:
        redis_client = get_redis()
        # Try to get exclusive lock for table creation
        lock = redis_client.set("db_create_lock", "1", nx=True, ex=30)
        if lock:
            logger.info("Creating database tables (dev mode)...")
            Base.metadata.create_all(bind=engine, checkfirst=True)
            logger.info("Database tables created")
        else:
            logger.info("Skipping table creation - another worker is handling it")
            time.sleep(2)
    except (SQLAlchemyError, RedisError) as e:
        logger.warning(f"Table creation error (non-fatal): {str(e)}")


def _check_redis_connection() -> bool:
    """Verify Redis connection is working."""
    try:
        redis_client = get_redis()
        redis_client.ping()
        logger.info("Redis connection successful")
        return True
    except RedisError as e:
        logger.error(f"Redis connection error: {str(e)}")
        return False


def _run_initial_fetch():
    """Run the initial news fetch in background thread if targets not met.

    Also checks inventory health and triggers top-up if needed.
    """
    time.sleep(3)  # Give app time to fully start
    logger.info("=" * 50)
    logger.info("INITIAL FETCH: Checking if ingestion needed...")
    try:
        from app.db.base import SessionLocal
        from app.ingestion.time import get_ingestion_day
        from app.models.ingestion_budget import IngestionBudget
        from app.repositories.ingestion_progress_repo import IngestionProgressRepository
        from app.services.topup_service import startup_inventory_check

        # First: check inventory health and trigger top-up if needed
        logger.info("INITIAL FETCH: Running inventory health check...")
        startup_inventory_check(SessionLocal)

        db = SessionLocal()
        try:
            day = get_ingestion_day()
            repo = IngestionProgressRepository(db)

            # Log current budget status
            budgets = db.query(IngestionBudget).filter(IngestionBudget.day == day).all()
            for b in budgets:
                remaining = max(0, int(b.target or 0) - int(b.inserted or 0) - int(b.reserved or 0))
                logger.info(
                    f"INITIAL FETCH: Budget {b.content_type.value} target={b.target} inserted={b.inserted} reserved={b.reserved} remaining={remaining}"
                )

            # Check if any progress rows exist for today
            all_rows = repo.list_for_day(day_utc=day)
            incomplete = repo.list_incomplete(day_utc=day)

            # Log incomplete breakdown by source type
            by_type = {}
            for r in incomplete:
                by_type.setdefault(r.source_type, []).append(r.feed_name)
            for st, feeds in by_type.items():
                logger.info(f"INITIAL FETCH: Incomplete {st}: {len(feeds)} feeds")

            if not all_rows:
                # Fresh start - no rows exist yet, run ingestion to create them
                logger.info("INITIAL FETCH: No progress rows exist - running initial ingestion")
                fetch_and_process_news()
                logger.info("INITIAL FETCH: Completed")
            elif incomplete:
                logger.info(
                    f"INITIAL FETCH: {len(incomplete)} feeds incomplete - running ingestion now"
                )
                fetch_and_process_news()
                logger.info("INITIAL FETCH: Completed")
            else:
                logger.info("INITIAL FETCH: All targets met - skipping")
        finally:
            db.close()
    except (SQLAlchemyError, RedisError) as e:
        logger.error(f"INITIAL FETCH: Error - {str(e)}", exc_info=True)
    logger.info("=" * 50)


def _start_scheduler() -> None:
    """Initialize legacy API-hosted scheduler only when explicitly enabled.

    Production worker lanes run outside the API process. Keeping this path
    behind a separate flag prevents accidental double execution if someone
    toggles ``SCHEDULER_ENABLED`` on the web service.
    """
    legacy_api_scheduler_enabled = os.getenv(
        "API_LEGACY_SCHEDULER_ENABLED",
        "false",
    ).lower() in {"true", "1", "yes", "on"}
    if not legacy_api_scheduler_enabled:
        logger.info("API legacy scheduler disabled; worker lanes own background jobs")
        return

    try:
        from app.scheduler import init_scheduler

        redis_client = get_redis()

        # Try to acquire a leader lock (process-unique token) with TTL.
        token = f"{os.getpid()}:{uuid.uuid4()}"
        lock_acquired = redis_client.set(
            SCHEDULER_LOCK_KEY,
            token,
            nx=True,
            ex=SCHEDULER_LOCK_TTL_SECONDS,
        )

        if not lock_acquired:
            logger.info("Legacy API scheduler already running elsewhere - skipping init")
            return

        logger.info("Acquired scheduler lock - initializing scheduler")
        scheduler = init_scheduler()
        if scheduler:
            # Keep the leader lock alive while this process is running.
            global _scheduler_lock_stop_event, _scheduler_lock_token, _scheduler_lock_redis
            _scheduler_lock_stop_event = threading.Event()
            _scheduler_lock_token = token
            _scheduler_lock_redis = redis_client

            def _refresh_lock_forever():
                # Refresh only if we still own the lock (token matches).
                lua = (
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) "
                    "else return 0 end"
                )
                while _scheduler_lock_stop_event and not _scheduler_lock_stop_event.is_set():
                    try:
                        result = redis_client.eval(
                            lua,
                            1,
                            SCHEDULER_LOCK_KEY,
                            token,
                            str(SCHEDULER_LOCK_TTL_SECONDS),
                        )
                        if int(result or 0) == 0:
                            logger.warning("Scheduler lock lost; stopping lock refresher")
                            _scheduler_lock_stop_event.set()
                            return
                    except RedisError as e:
                        logger.warning(f"Scheduler lock refresh error (non-fatal): {e}")
                    time.sleep(SCHEDULER_LOCK_REFRESH_SECONDS)

            threading.Thread(target=_refresh_lock_forever, daemon=True).start()

            logger.info("Scheduler initialized")

    except RedisError as e:
        logger.error(f"Error starting scheduler: {str(e)}")

    logger.info("Starting legacy initial fetch check in background")
    threading.Thread(target=_run_initial_fetch, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting up application")

    # Fail fast on malformed discovery registry config instead of waiting for
    # the first discovery window to touch it.
    from app.config.search_query_registry import get_search_query_registry

    get_search_query_registry()

    # Best-effort signal handling for graceful ingestion shutdown.
    try:
        from app.ingestion.checkpointing import install_signal_handlers

        install_signal_handlers()
    except (ImportError, OSError):
        pass

    if os.getenv("SKIP_STARTUP_CHECKS", "").lower() == "true":
        yield
        logger.info("Shutting down application")
        return

    _create_tables()
    _check_redis_connection()
    _start_scheduler()

    yield

    # Shutdown
    # Best-effort: stop lock refresher and release lock if owned.
    global _scheduler_lock_stop_event, _scheduler_lock_token, _scheduler_lock_redis
    if _scheduler_lock_stop_event is not None:
        _scheduler_lock_stop_event.set()
    if _scheduler_lock_redis is not None and _scheduler_lock_token is not None:
        try:
            lua = (
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) "
                "else return 0 end"
            )
            _scheduler_lock_redis.eval(lua, 1, SCHEDULER_LOCK_KEY, _scheduler_lock_token)
        except RedisError as e:
            logger.warning(f"Scheduler lock release error (non-fatal): {e}")
    _scheduler_lock_stop_event = None
    _scheduler_lock_token = None
    _scheduler_lock_redis = None

    logger.info("Shutting down application")


# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AI-powered tech news aggregator API. Fetches articles from RSS feeds and videos from YouTube, processes with AI summarization, and serves personalized feeds.",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
    lifespan=lifespan,
)

# CORS middleware — restrict origins
_cors_origins: list[str] = (
    [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if settings.CORS_ORIGINS
    else (["capacitor://localhost", "http://localhost"] if settings.ENV != "prod" else [])
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Admin-Key",
        "X-Admin-Share-Token",
        "X-Device-Country",
    ],
)

# Rate limiting (per IP)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.REDIS_URL,
)
app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit handler with structured error code."""
    from app.core.error_codes import ERROR_MESSAGES, ErrorCode

    code = ErrorCode.RATE_LIMITED
    return JSONResponse(
        status_code=429,
        content={
            "detail": str(exc.detail),
            "code": code.value,
            "message": ERROR_MESSAGES[code],
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Redirect admin UI auth failures to the login page."""
    admin_redirect = admin_ui_auth_redirect_response(
        request,
        status_code=exc.status_code,
    )
    if admin_redirect is not None:
        return admin_redirect
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def docs_guard(request: Request, call_next):
    """Hide documentation/schema endpoints when docs are disabled."""
    protected_paths = {
        "/docs",
        "/redoc",
        "/openapi.json",
        f"{settings.API_V1_STR}/openapi.json",
    }
    if request.url.path in protected_paths and not settings.DOCS_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


# ── Redis fail-closed middleware ────────────────────────────────────────
_redis_last_check: float = 0.0
_redis_healthy: bool = True
_REDIS_CHECK_INTERVAL = 5.0  # seconds


@app.middleware("http")
async def redis_health_guard(request: Request, call_next):
    """Fail-closed: reject requests if Redis is down (cached check every 5s)."""
    global _redis_last_check, _redis_healthy

    _skip_paths = (
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        f"{settings.API_V1_STR}/openapi.json",
    )
    if request.url.path in _skip_paths:
        return await call_next(request)

    now = time.monotonic()
    if now - _redis_last_check > _REDIS_CHECK_INTERVAL:
        try:
            r = get_redis()
            r.ping()
            _redis_healthy = True
        except Exception:
            _redis_healthy = False
            logger.error("Redis unreachable - fail-closed rate limiting active")
        _redis_last_check = now

    if not _redis_healthy:
        from app.core.error_codes import ERROR_MESSAGES, ErrorCode

        code = ErrorCode.SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Rate limiting service unavailable",
                "code": code.value,
                "message": ERROR_MESSAGES[code],
            },
        )

    return await call_next(request)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add request processing time to response headers and record metrics."""
    from app.core.observability import metrics_collector

    start_time = time.time()
    response = await call_next(request)
    latency_ms = (time.time() - start_time) * 1000

    response.headers["X-Process-Time"] = str(latency_ms / 1000)

    # Record metrics for non-static paths
    path = request.url.path
    if not path.startswith("/static") and path != "/favicon.ico":
        is_error = response.status_code >= 400
        metrics_collector.record_request(path, latency_ms, is_error)

    return response


# Exception handlers for custom exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions globally with structured error codes."""
    from app.core.error_codes import ERROR_MESSAGES, ErrorCode
    from app.core.exceptions import (
        ArticleNotFoundError,
        ContentNotFoundError,
        ExternalServiceError,
        FeedFetchError,
        LLMConfigurationError,
        LLMQuotaExceededError,
        NotFoundError,
        QuotaExceededError,
        ValidationError,
        VideoNotFoundError,
    )

    def _err(status: int, code: ErrorCode, detail: Optional[str] = None):
        return JSONResponse(
            status_code=status,
            content={
                "detail": detail or ERROR_MESSAGES[code],
                "code": code.value,
                "message": ERROR_MESSAGES[code],
            },
        )

    # Handle specific exception types (most-specific first)
    if isinstance(exc, ArticleNotFoundError):
        return _err(404, ErrorCode.ARTICLE_NOT_FOUND, exc.message)

    if isinstance(exc, VideoNotFoundError):
        return _err(404, ErrorCode.VIDEO_NOT_FOUND, exc.message)

    if isinstance(exc, ContentNotFoundError):
        return _err(404, ErrorCode.CONTENT_NOT_FOUND, exc.message)

    if isinstance(exc, NotFoundError):
        return _err(404, ErrorCode.NOT_FOUND, exc.message)

    if isinstance(exc, QuotaExceededError):
        return _err(429, ErrorCode.QUOTA_EXCEEDED, exc.message)

    if isinstance(exc, LLMQuotaExceededError):
        logger.warning(f"LLM quota exceeded: {exc.message}")
        return _err(503, ErrorCode.LLM_QUOTA_EXCEEDED, exc.message)

    if isinstance(exc, LLMConfigurationError):
        logger.error(f"LLM configuration error: {exc.message}")
        return _err(503, ErrorCode.LLM_CONFIGURATION_ERROR, exc.message)

    if isinstance(exc, FeedFetchError):
        logger.error(f"Feed fetch error: {exc.message}", exc_info=True)
        return _err(502, ErrorCode.FEED_FETCH_ERROR, exc.message)

    if isinstance(exc, ValidationError):
        return _err(400, ErrorCode.VALIDATION_ERROR, exc.message)

    if isinstance(exc, ExternalServiceError):
        logger.error(f"External service error: {exc.message}", exc_info=True)
        return _err(502, ErrorCode.EXTERNAL_SERVICE_ERROR)

    # Generic unhandled exception
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return _err(500, ErrorCode.INTERNAL_ERROR)


# Register API routes
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring.

    Validates DB and Redis connectivity plus scheduler visibility. Render polls
    this endpoint frequently, so we intentionally skip the heavier ingestion
    metrics queries here to avoid turning a transient database issue into API
    pool starvation. Deeper ingestion diagnostics remain available via the
    admin ops endpoints.
    """
    from app.core.observability import get_runtime_health

    health = get_runtime_health(include_ingestion_checks=False)

    if health["status"] == "unhealthy":
        # Send alert for health check failure
        try:
            from app.services.alerting_service import alert_health_check_failed

            alert_health_check_failed(
                database_status=health["checks"].get("database", {}).get("status", "unknown"),
                redis_status=health["checks"].get("redis", {}).get("status", "unknown"),
            )
        except Exception as e:
            logger.warning(f"Failed to send health check alert: {e}")
        return JSONResponse(status_code=503, content=health)

    return health


def _get_ingestion_health_metrics() -> dict:
    """Get ingestion health metrics for the /metrics endpoint."""
    try:
        from app.scheduler.tasks_health import get_ingestion_metrics

        return get_ingestion_metrics()
    except Exception as e:
        logger.warning(f"Failed to get ingestion health metrics: {e}")
        return {"error": str(e)}


@app.get("/metrics", dependencies=[Depends(require_admin_key)])
def metrics():
    """Lightweight JSON metrics for ingestion progress (requires ADMIN_API_KEY)."""
    from app.ingestion.runtime_state import get_scheduler_snapshot
    from app.ingestion.time import get_ingestion_day
    from app.models.ingestion_progress import IngestionProgress

    today = get_ingestion_day()

    db = SessionLocal()
    try:
        rows = (
            db.query(IngestionProgress)
            .filter(IngestionProgress.day_utc == today)
            .order_by(IngestionProgress.source_type.asc(), IngestionProgress.feed_name.asc())
            .all()
        )

        scheduler_snapshot = get_scheduler_snapshot()

        budgets = []
        try:
            from app.models.ingestion_budget import IngestionBudget

            budgets = (
                db.query(IngestionBudget)
                .filter(IngestionBudget.day == today)
                .order_by(IngestionBudget.content_type.asc())
                .all()
            )
        except SQLAlchemyError as e:
            logger.warning(f"Failed to query ingestion budgets: {e}")
            budgets = []

        feeds = [
            {
                "source_type": r.source_type,
                "feed_name": r.feed_name,
                "status": r.status,
                "items_ingested": int(r.items_ingested or 0),
                "items_attempted": int(getattr(r, "items_attempted", 0) or 0),
                "target": int(r.target or 0),
                "last_item_cursor": r.last_item_cursor,
                "retry_count": int(getattr(r, "retry_count", 0) or 0),
                "retry_at": r.retry_at.isoformat() if getattr(r, "retry_at", None) else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "last_error": r.last_error,
            }
            for r in rows
        ]

        totals_by_type = {"ARTICLE": 0, "VIDEO": 0, "REEL": 0}
        attempted_by_type = {"ARTICLE": 0, "VIDEO": 0, "REEL": 0}
        target_by_type = {"ARTICLE": 0, "VIDEO": 0, "REEL": 0}

        for f in feeds:
            st = f["source_type"]
            if st == "rss":
                ct = "ARTICLE"
            elif st == "youtube_video":
                ct = "VIDEO"
            elif st == "youtube_reel":
                ct = "REEL"
            else:
                ct = st

            totals_by_type.setdefault(ct, 0)
            attempted_by_type.setdefault(ct, 0)
            target_by_type.setdefault(ct, 0)
            totals_by_type[ct] += int(f["items_ingested"])
            attempted_by_type[ct] += int(f["items_attempted"])
            target_by_type[ct] += int(f["target"])

        return {
            "ingestion_day": today.isoformat(),
            "day_utc": today.isoformat(),
            "feeds": feeds,
            "scheduler": scheduler_snapshot,
            "budgets": [
                {
                    "day": b.day.isoformat(),
                    "content_type": b.content_type.value,
                    "target": int(b.target or 0),
                    "reserved": int(b.reserved or 0),
                    "inserted": int(b.inserted or 0),
                    "remaining": max(
                        0, int(b.target or 0) - int(b.inserted or 0) - int(b.reserved or 0)
                    ),
                    "seen": int(b.seen or 0),
                    "suppressed": int(b.suppressed or 0),
                    "attempts": int(b.attempts or 0),
                    "updated_at": b.updated_at.isoformat() if b.updated_at else None,
                }
                for b in budgets
            ],
            "totals": {
                "items_ingested": sum(f["items_ingested"] for f in feeds),
                "items_attempted": sum(f["items_attempted"] for f in feeds),
                "target": sum(f["target"] for f in feeds),
                "feeds_complete": sum(1 for f in feeds if f["status"] == "complete"),
                "feeds_total": len(feeds),
                "by_content_type": {
                    "items_ingested": totals_by_type,
                    "items_attempted": attempted_by_type,
                    "target": target_by_type,
                },
            },
            "ingestion_health": _get_ingestion_health_metrics(),
        }
    finally:
        db.close()


@app.get("/ops/status", dependencies=[Depends(require_admin_key)])
def operational_status():
    """
    Comprehensive operational status endpoint (requires ADMIN_API_KEY).

    Returns:
    - Request metrics (last 60 minutes): total requests, error rate, latencies
    - Connection pool stats: Redis and DB pool utilization
    - Service health indicators

    Use this for operational monitoring dashboards and alerting.
    """
    from app.core.observability import get_operational_status

    return get_operational_status()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
