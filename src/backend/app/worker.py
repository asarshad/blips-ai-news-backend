"""
Standalone worker process for background jobs.

This module runs the scheduler in a dedicated process, separate from the web API.
It handles all background tasks like news fetching, scoring, and clustering.

Usage:
    python -m app.worker
"""

import os
import sys
import time
import signal
import uuid

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.dependencies import get_redis
from app.scheduler import init_scheduler
from app.scheduler.tasks import fetch_and_process_news
from redis.exceptions import RedisError

# Configure logging
setup_logging()
logger = get_logger(__name__)

# Global scheduler reference for graceful shutdown
_scheduler = None

# Unique token used to verify lock ownership
_worker_lock_token: str = f"{os.getpid()}:{uuid.uuid4()}"

WORKER_LOCK_KEY = "worker_lock"
WORKER_LOCK_TTL = 300  # 5 minutes


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    sys.exit(0)


def acquire_worker_lock() -> bool:
    """
    Acquire a distributed lock to ensure only one worker runs.
    Uses a unique token so only the owning process can refresh/release.
    
    Returns:
        True if lock acquired, False otherwise
    """
    try:
        redis_client = get_redis()
        acquired = redis_client.set(
            WORKER_LOCK_KEY,
            _worker_lock_token,
            nx=True,
            ex=WORKER_LOCK_TTL,
        )
        return bool(acquired)
    except RedisError as e:
        logger.error(f"Failed to acquire worker lock: {e}")
        return False


# Lua script: only refresh TTL if caller still owns the lock (CAS).
_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "  return redis.call('expire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)


def refresh_worker_lock() -> bool:
    """Refresh the worker lock TTL — only if we still own it."""
    try:
        redis_client = get_redis()
        result = redis_client.eval(
            _REFRESH_LUA, 1, WORKER_LOCK_KEY, _worker_lock_token, str(WORKER_LOCK_TTL)
        )
        if int(result or 0) == 0:
            logger.warning("Worker lock lost — another process owns it")
            return False
        return True
    except RedisError as e:
        logger.warning(f"Failed to refresh worker lock: {e}")
        return False


def run_worker():
    """Main worker entry point."""
    global _scheduler
    
    logger.info("=" * 60)
    logger.info("Blips Worker Starting")
    logger.info(f"Environment: {os.getenv('ENV', 'dev')}")
    logger.info(f"Scheduler enabled: {os.getenv('SCHEDULER_ENABLED', 'true')}")
    logger.info(f"Ingestion enabled: {os.getenv('INGESTION_ENABLED', 'true')}")
    logger.info(f"Feature ingestion: {os.getenv('FEATURE_INGESTION_ENABLED', 'not set')}")
    logger.info(f"Fetch interval: {os.getenv('NEWS_FETCH_INTERVAL_MINUTES', '30')} minutes")
    logger.info("=" * 60)
    
    # Check if scheduler is enabled
    scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    if not scheduler_enabled:
        logger.info("Scheduler is disabled via SCHEDULER_ENABLED=false")
        logger.info("Worker will exit.")
        return
    
    # Try to acquire the worker lock
    if not acquire_worker_lock():
        logger.warning("Another worker is already running. Exiting.")
        return
    
    logger.info("Worker lock acquired successfully")
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Initialize scheduler
    _scheduler = init_scheduler()
    if not _scheduler:
        logger.error("Failed to initialize scheduler")
        return
    
    logger.info("Scheduler initialized successfully")
    
    # Run initial fetch
    logger.info("Running initial news fetch...")
    try:
        fetch_and_process_news()
        logger.info("Initial fetch completed")
    except RedisError as e:
        logger.error(f"Initial fetch failed: {e}")
    
    # Keep the worker running and refresh lock
    logger.info("Worker running. Press Ctrl+C to stop.")
    try:
        while True:
            if not refresh_worker_lock():
                logger.error("Lost worker lock — shutting down to avoid dual execution")
                break
            time.sleep(60)  # Refresh lock every minute
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker shutting down...")
        if _scheduler:
            _scheduler.shutdown(wait=True)
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    run_worker()
