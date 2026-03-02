"""
Standalone worker process for background jobs.

This module runs the scheduler in a dedicated process, separate from the web API.
It handles all background tasks like news fetching, scoring, and clustering.

Usage:
    python -m app.worker
"""

import os
import signal
import sys
import threading
import time
import traceback
import uuid

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Bootstrap logging immediately so every subsequent line is captured.
# Use a plain print() fallback in case the import itself fails.
try:
    from app.core.logging import get_logger, setup_logging
    setup_logging()
    logger = get_logger(__name__)
except Exception as _log_exc:  # noqa: BLE001
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger(__name__)
    logger.error("Logging bootstrap failed: %s", _log_exc)

# Deferred imports — failures here are caught inside run_worker() so the
# full traceback appears in Render logs rather than a silent crash.
_heavy_imports_ok = False
try:
    from redis.exceptions import RedisError

    from app.core.dependencies import get_redis
    from app.scheduler import init_scheduler
    from app.scheduler.tasks import fetch_and_process_news
    _heavy_imports_ok = True
except Exception as _import_exc:  # noqa: BLE001
    logger.critical(
        "Fatal import error — worker cannot start: %s\n%s",
        _import_exc,
        traceback.format_exc(),
    )
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(1)

# Global scheduler reference for graceful shutdown
_scheduler = None

# Shared stop event — also wired into the ingestion checkpointing module
# so ThreadPoolExecutors stop accepting new work before we tear down.
_stop_event = threading.Event()

# Unique token used to verify lock ownership
_worker_lock_token: str = f"{os.getpid()}:{uuid.uuid4()}"

WORKER_LOCK_KEY = "worker_lock"
WORKER_LOCK_TTL = 300  # 5 minutes


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully.

    Sets the shared stop event so in-flight ingestion / ThreadPoolExecutors
    finish their current batch and stop accepting new futures *before* we
    tear down the APScheduler.
    """
    logger.info(f"Received signal {signum}, requesting graceful shutdown…")
    _stop_event.set()
    # Also propagate to the ingestion checkpointing module.
    try:
        from app.ingestion.checkpointing import STOP_EVENT
        STOP_EVENT.set()
    except Exception:
        pass


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


def _acquire_lock_with_retry(max_attempts: int = 10, base_delay: float = 5.0) -> bool:
    """Try to acquire the worker lock with exponential back-off.

    Handles two common transient failures:
    - Redis not yet reachable (cold start / network delay)
    - Stale lock left by a previously-crashed worker (waits for TTL expiry)
    """
    for attempt in range(1, max_attempts + 1):
        try:
            if acquire_worker_lock():
                return True
            delay = min(base_delay * (2 ** (attempt - 1)), WORKER_LOCK_TTL)
            logger.warning(
                f"Lock held by another process (attempt {attempt}/{max_attempts}). "
                f"Retrying in {delay:.0f}s …"
            )
            time.sleep(delay)
        except Exception as e:
            delay = min(base_delay * (2 ** (attempt - 1)), 60)
            logger.warning(
                f"Lock acquisition error (attempt {attempt}/{max_attempts}): {e}. "
                f"Retrying in {delay:.0f}s …"
            )
            time.sleep(delay)
    return False


def run_worker():
    """Main worker entry point."""
    global _scheduler

    # Register signal handlers as early as possible so SIGTERM during lock
    # acquisition is handled gracefully rather than causing an immediate exit.
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info("=" * 60)
    logger.info("Blips Worker Starting")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Environment: {os.getenv('ENV', 'dev')}")
    logger.info(f"Scheduler enabled: {os.getenv('SCHEDULER_ENABLED', 'true')}")
    logger.info(f"Ingestion enabled: {os.getenv('INGESTION_ENABLED', 'true')}")
    logger.info(f"Feature ingestion: {os.getenv('FEATURE_INGESTION_ENABLED', 'not set')}")
    logger.info(f"Fetch interval: {os.getenv('NEWS_FETCH_INTERVAL_MINUTES', '30')} minutes")
    logger.info("=" * 60)
    sys.stdout.flush()

    # Check if scheduler is enabled
    scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    if not scheduler_enabled:
        logger.info("Scheduler is disabled via SCHEDULER_ENABLED=false")
        # Sleep forever so Render doesn't restart in a tight loop
        logger.info("Worker idling (SCHEDULER_ENABLED=false).")
        sys.stdout.flush()
        _idle_forever()
        return

    # Try to acquire the worker lock (with retry / back-off)
    if not _acquire_lock_with_retry():
        logger.error("Could not acquire worker lock after retries — idling")
        sys.stdout.flush()
        _idle_forever()
        return

    logger.info("Worker lock acquired successfully")
    sys.stdout.flush()
    
    # Initialize scheduler (retry on transient failure)
    for attempt in range(1, 4):
        try:
            _scheduler = init_scheduler()
        except Exception as sched_exc:
            logger.error(
                f"Scheduler init exception (attempt {attempt}/3): {sched_exc}\n"
                + traceback.format_exc()
            )
            _scheduler = None
        if _scheduler:
            break
        logger.warning(f"Scheduler init failed (attempt {attempt}/3), retrying in 10s …")
        sys.stdout.flush()
        time.sleep(10)

    if not _scheduler:
        logger.error("Failed to initialize scheduler after 3 attempts — idling")
        sys.stdout.flush()
        _idle_forever()
        return

    logger.info("Scheduler initialized successfully")
    sys.stdout.flush()

    # Run initial fetch (catch ALL errors so it never kills the worker)
    logger.info("Running initial news fetch...")
    sys.stdout.flush()
    try:
        fetch_and_process_news()
        logger.info("Initial fetch completed")
    except Exception as e:
        logger.error(
            f"Initial fetch failed (non-fatal): {e}\n" + traceback.format_exc()
        )
    sys.stdout.flush()

    # Keep the worker running and refresh lock
    logger.info("Worker running. Press Ctrl+C to stop.")
    sys.stdout.flush()
    try:
        while not _stop_event.is_set():
            if not refresh_worker_lock():
                logger.error("Lost worker lock — shutting down to avoid dual execution")
                break
            _stop_event.wait(timeout=60)  # Wakes immediately on signal
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Worker shutting down…")
        _stop_event.set()  # Ensure everything knows we're stopping
        if _scheduler:
            _scheduler.shutdown(wait=False)
        logger.info("Worker shutdown complete")
        sys.stdout.flush()


def _idle_forever():
    """Block the process indefinitely so Render doesn't restart in a tight loop."""
    try:
        while not _stop_event.is_set():
            _stop_event.wait(timeout=3600)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    try:
        run_worker()
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "Unhandled exception in run_worker — worker is exiting: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
