"""
FastAPI application entry point.

This module configures and creates the FastAPI application with:
- CORS middleware
- Request timing middleware  
- Global exception handling
- API route registration
- Background scheduler initialization
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.dependencies import get_redis
from app.api import api_router
from app.db.base import Base, engine
from app.scheduler import init_scheduler
from app.scheduler.tasks import fetch_and_process_news

# Configure logging first
setup_logging()
logger = get_logger(__name__)


def _create_tables() -> None:
    """Create database tables if they don't exist.
    
    Uses a Redis lock to prevent race conditions with multiple workers.
    Handles existing enum types gracefully (common on redeployments).
    """
    try:
        redis_client = get_redis()
        # Try to get exclusive lock for table creation
        lock = redis_client.set("db_create_lock", "1", nx=True, ex=30)
        if lock:
            logger.info("Creating database tables...")
            # Use checkfirst=True to skip existing objects
            Base.metadata.create_all(bind=engine, checkfirst=True)
            logger.info("Database tables created")
        else:
            logger.info("Skipping table creation - another worker is handling it")
            # Give the other worker time to finish
            import time
            time.sleep(2)
    except Exception as e:
        # Ignore "already exists" errors (common on redeployments)
        error_msg = str(e).lower()
        if "already exists" in error_msg or "duplicate" in error_msg:
            logger.info("Database objects already exist - skipping creation")
        else:
            logger.warning(f"Table creation error: {str(e)}")


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


import os
import threading


def _run_initial_fetch():
    """Run the initial news fetch in background thread."""
    import time
    time.sleep(2)  # Give app time to fully start
    logger.info("Running initial news fetch in background thread")
    try:
        fetch_and_process_news()
    except Exception as e:
        logger.error(f"Initial fetch error: {str(e)}")


def _start_scheduler() -> None:
    """Initialize background scheduler and run initial fetch.
    
    IMPORTANT: In production, scheduler runs ONLY in the worker service.
    The web API should have SCHEDULER_ENABLED=false to prevent duplicate jobs.
    """
    # Check if scheduler is enabled (disabled on web service in production)
    scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    if not scheduler_enabled:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false (running in worker)")
        return
    
    try:
        redis_client = get_redis()
        
        # Try to acquire a lock with 60 second expiry
        lock_acquired = redis_client.set(
            "scheduler_lock", 
            "1", 
            nx=True,  # Only set if doesn't exist
            ex=60     # Expire after 60 seconds (will be refreshed by scheduler)
        )
        
        if not lock_acquired:
            logger.info("Scheduler already running on another worker - skipping init")
            return
        
        logger.info("Acquired scheduler lock - initializing scheduler")
        scheduler = init_scheduler()
        if scheduler:
            logger.info("Scheduler initialized - running initial fetch in background")
            # Start background thread for initial fetch (doesn't block startup)
            thread = threading.Thread(target=_run_initial_fetch, daemon=True)
            thread.start()
            
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting up application")
    _check_redis_connection()
    _start_scheduler()
    
    yield
    
    # Shutdown
    logger.info("Shutting down application")


# Create database tables
_create_tables()

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
    """Health check endpoint for monitoring."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
