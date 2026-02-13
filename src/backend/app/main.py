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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.api import api_router
from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.logging import get_logger, setup_logging
from app.db.base import Base, SessionLocal, engine
from app.scheduler import init_scheduler
from app.scheduler.tasks import fetch_and_process_news

# Configure logging first
setup_logging()
logger = get_logger(__name__)


SCHEDULER_LOCK_KEY = "scheduler_lock"
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
    except Exception as e:
        logger.warning(f"Table creation error (non-fatal): {str(e)}")


def _check_redis_connection() -> bool:
    """Verify Redis connection is working."""
    try:
        redis_client = get_redis()
        redis_client.ping()
        logger.info("Redis connection successful")
        return True
    except Exception as e:
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
                logger.info(f"INITIAL FETCH: Budget {b.content_type.value} target={b.target} inserted={b.inserted} reserved={b.reserved} remaining={remaining}")
            
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
                logger.info(f"INITIAL FETCH: {len(incomplete)} feeds incomplete - running ingestion now")
                fetch_and_process_news()
                logger.info("INITIAL FETCH: Completed")
            else:
                logger.info("INITIAL FETCH: All targets met - skipping")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"INITIAL FETCH: Error - {str(e)}", exc_info=True)
    logger.info("=" * 50)


def _start_scheduler() -> None:
    """Initialize background scheduler and run initial fetch.
    
    Preferred: run scheduler in a dedicated worker service.
    Single-service deployments may run scheduler in the API service by setting
    `SCHEDULER_ENABLED=true`. A Redis leader lock ensures only one process runs it.
    """
    # Check if scheduler is enabled (disabled on web service in production)
    scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    if not scheduler_enabled:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false (running in worker)")
        return
    
    try:
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
            logger.info("Scheduler already running on another worker - skipping scheduler init")
            # Still run initial fetch - important for resuming after restarts
            logger.info("Starting initial fetch check in background (non-leader)")
            threading.Thread(target=_run_initial_fetch, daemon=True).start()
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
                    except Exception as e:
                        logger.warning(f"Scheduler lock refresh error (non-fatal): {e}")
                    time.sleep(SCHEDULER_LOCK_REFRESH_SECONDS)

            threading.Thread(target=_refresh_lock_forever, daemon=True).start()

            logger.info("Scheduler initialized")
            
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}")
    
    # Always run initial fetch check regardless of scheduler lock
    # This ensures we resume ingestion after restart if targets not met
    logger.info("Starting initial fetch check in background")
    threading.Thread(target=_run_initial_fetch, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting up application")

    # Best-effort signal handling for graceful ingestion shutdown.
    try:
        from app.ingestion.checkpointing import install_signal_handlers

        install_signal_handlers()
    except Exception:
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
        except Exception as e:
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
    else ["capacitor://localhost", "http://localhost"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (per IP)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.REDIS_URL,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add request processing time to response headers."""
    start_time = time.time()
    response = await call_next(request)
    response.headers["X-Process-Time"] = str(time.time() - start_time)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions globally."""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred"}
    )


# Register API routes
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring.

    Validates DB and Redis connectivity for Render's readiness probe.
    Returns 503 if any critical dependency is unreachable.
    """
    checks: dict = {"status": "healthy"}
    is_healthy = True

    # Check database
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        finally:
            db.close()
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        is_healthy = False

    # Check Redis
    try:
        redis_client = get_redis()
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
        is_healthy = False

    if not is_healthy:
        checks["status"] = "unhealthy"
        return JSONResponse(status_code=503, content=checks)

    return checks


@app.get("/metrics")
def metrics():
    """Lightweight JSON metrics for ingestion progress."""
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
        except Exception as e:
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
                    "remaining": max(0, int(b.target or 0) - int(b.inserted or 0) - int(b.reserved or 0)),
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
        }
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
