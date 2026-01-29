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

from app.api import api_router
from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.logging import get_logger, setup_logging
from app.db.base import Base, engine
from app.db.base import SessionLocal
from app.scheduler import init_scheduler
from app.scheduler.tasks import fetch_and_process_news
from sqlalchemy import text

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
    """Run the initial news fetch in background thread."""
    time.sleep(2)  # Give app time to fully start
    logger.info("Running initial news fetch in background thread")
    try:
        fetch_and_process_news()
    except Exception as e:
        logger.error(f"Initial fetch error: {str(e)}")


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
            logger.info("Scheduler already running on another worker - skipping init")
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

            logger.info("Scheduler initialized - running initial fetch in background")
            # Start background thread for initial fetch (doesn't block startup)
            threading.Thread(target=_run_initial_fetch, daemon=True).start()
            
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}")


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
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# CORS middleware - allow all origins in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    Returns 200 only when the app and DB are reachable.
    """
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e)})

    return {"status": "healthy"}


@app.get("/metrics")
def metrics():
    """Lightweight JSON metrics for ingestion progress."""
    from datetime import datetime

    from app.models.ingestion_progress import IngestionProgress

    today = datetime.utcnow().date()

    db = SessionLocal()
    try:
        rows = (
            db.query(IngestionProgress)
            .filter(IngestionProgress.day_utc == today)
            .order_by(IngestionProgress.source_type.asc(), IngestionProgress.feed_name.asc())
            .all()
        )

        feeds = [
            {
                "source_type": r.source_type,
                "feed_name": r.feed_name,
                "status": r.status,
                "items_ingested": int(r.items_ingested or 0),
                "target": int(r.target or 0),
                "last_item_cursor": r.last_item_cursor,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "last_error": r.last_error,
            }
            for r in rows
        ]

        return {
            "day_utc": today.isoformat(),
            "feeds": feeds,
            "totals": {
                "items_ingested": sum(f["items_ingested"] for f in feeds),
                "target": sum(f["target"] for f in feeds),
                "feeds_complete": sum(1 for f in feeds if f["status"] == "complete"),
                "feeds_total": len(feeds),
            },
        }
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
