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

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.dependencies import get_redis
from app.scheduler import init_scheduler
from app.scheduler.tasks import fetch_and_process_news

# Configure logging
setup_logging()
logger = get_logger(__name__)

# Global scheduler reference for graceful shutdown
_scheduler = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    sys.exit(0)


def acquire_worker_lock() -> bool:
    """
    Acquire a distributed lock to ensure only one worker runs.
    
    Returns:
        True if lock acquired, False otherwise
    """
    try:
        redis_client = get_redis()
        # Try to acquire lock with 5 minute expiry (refreshed by heartbeat)
        acquired = redis_client.set(
            "worker_lock",
            os.getpid(),
            nx=True,
            ex=300
        )
        return bool(acquired)
    except Exception as e:
        logger.error(f"Failed to acquire worker lock: {e}")
        return False


def refresh_worker_lock():
    """Refresh the worker lock TTL."""
    try:
        redis_client = get_redis()
        redis_client.expire("worker_lock", 300)
    except Exception as e:
        logger.warning(f"Failed to refresh worker lock: {e}")


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
    except Exception as e:
        logger.error(f"Initial fetch failed: {e}")
    
    # Keep the worker running and refresh lock
    logger.info("Worker running. Press Ctrl+C to stop.")
    try:
        while True:
            refresh_worker_lock()
            time.sleep(60)  # Refresh lock every minute
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker shutting down...")
        if _scheduler:
            _scheduler.shutdown(wait=True)
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    run_worker()
