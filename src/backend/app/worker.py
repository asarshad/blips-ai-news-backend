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
    from app.scheduler import _resolve_ingestion_minutes, init_scheduler
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

WORKER_LOCK_KEY = os.getenv("SCHEDULER_LEADER_LOCK_KEY", "scheduler_lock")
WORKER_LOCK_TTL = int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "120"))
LOCK_REACQUIRE_ATTEMPTS = 3
LOCK_REACQUIRE_DELAY_SECONDS = 5.0


def signal_handler(signum, _frame):
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


def acquire_worker_lock() -> bool | None:
    """
    Acquire a distributed lock to ensure only one worker runs.
    Uses a unique token so only the owning process can refresh/release.

    Returns:
        True if lock acquired, False if another worker owns it,
        None if Redis was unavailable while checking
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
        return None


# Lua script: only refresh TTL if caller still owns the lock (CAS).
_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "  return redis.call('expire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "  return redis.call('del', KEYS[1]) "
    "else return 0 end"
)


def refresh_worker_lock() -> bool | None:
    """Refresh the worker lock TTL — only if we still own it.

    Returns:
        True  — lock refreshed successfully.
        False — lock is now owned by another process (fatal: worker should stop).
        None  — transient Redis error (caller should tolerate a few retries).
    """
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
        logger.warning(f"Failed to refresh worker lock (transient Redis error): {e}")
        return None


def release_worker_lock() -> bool:
    """Release the worker lock only if we still own it."""
    try:
        redis_client = get_redis()
        result = redis_client.eval(_RELEASE_LUA, 1, WORKER_LOCK_KEY, _worker_lock_token)
        return int(result or 0) == 1
    except RedisError as e:
        logger.warning(f"Failed to release worker lock: {e}")
        return False


def _probe_worker_lock_state() -> str:
    """Inspect lock ownership state.

    Returns:
        "ours"    -> lock token matches this process
        "other"   -> lock is owned by another process
        "missing" -> key is absent (possible TTL expiry/eviction)
        "unknown" -> Redis unavailable while probing
    """
    try:
        redis_client = get_redis()
        raw_owner = redis_client.get(WORKER_LOCK_KEY)
        if raw_owner is None:
            return "missing"

        owner = (
            raw_owner.decode("utf-8", errors="replace")
            if isinstance(raw_owner, (bytes, bytearray))
            else str(raw_owner)
        )
        if owner == _worker_lock_token:
            return "ours"
        return "other"
    except RedisError as e:
        logger.warning(f"Unable to probe worker lock owner (transient Redis error): {e}")
        return "unknown"


def _attempt_lock_reacquire() -> bool:
    """Try to recover from a lock refresh miss without spawning dual workers.

    We only attempt reacquire when the key appears missing/unknown. If another
    token is definitively present, we return False immediately to avoid dual
    execution.
    """
    for attempt in range(1, LOCK_REACQUIRE_ATTEMPTS + 1):
        state = _probe_worker_lock_state()
        if state == "ours":
            logger.warning("Worker lock refresh missed, but ownership is still ours")
            return True
        if state == "other":
            logger.error("Worker lock is owned by another process; cannot reacquire safely")
            return False

        if acquire_worker_lock() is True:
            logger.warning(
                "Worker lock was missing and has been reacquired "
                f"(attempt {attempt}/{LOCK_REACQUIRE_ATTEMPTS})"
            )
            return True

        if attempt < LOCK_REACQUIRE_ATTEMPTS:
            logger.warning(
                "Worker lock not reacquired yet "
                f"(attempt {attempt}/{LOCK_REACQUIRE_ATTEMPTS}); retrying in "
                f"{LOCK_REACQUIRE_DELAY_SECONDS:.0f}s"
            )
            _stop_event.wait(timeout=LOCK_REACQUIRE_DELAY_SECONDS)
            if _stop_event.is_set():
                return False

    logger.error(
        f"Failed to reacquire worker lock after {LOCK_REACQUIRE_ATTEMPTS} attempts; shutting down"
    )
    return False


def _acquire_lock_with_retry(max_attempts: int = 10, base_delay: float = 5.0) -> bool:
    """Try to acquire the worker lock with exponential back-off.

    Handles two common transient failures:
    - Redis not yet reachable (cold start / network delay)
    - Stale lock left by a previously-crashed worker (waits for TTL expiry)
    """
    for attempt in range(1, max_attempts + 1):
        try:
            lock_result = acquire_worker_lock()
            if lock_result is True:
                return True
            delay_cap = 60 if lock_result is None else WORKER_LOCK_TTL
            delay = min(base_delay * (2 ** (attempt - 1)), delay_cap)
            if lock_result is None:
                logger.warning(
                    f"Redis unavailable during worker lock acquisition "
                    f"(attempt {attempt}/{max_attempts}). Retrying in {delay:.0f}s …"
                )
            else:
                logger.warning(
                    f"Lock held by another process (attempt {attempt}/{max_attempts}). "
                    f"Retrying in {delay:.0f}s …"
                )
            if _stop_event.wait(timeout=delay):
                return False
        except Exception as e:
            delay = min(base_delay * (2 ** (attempt - 1)), 60)
            logger.warning(
                f"Lock acquisition error (attempt {attempt}/{max_attempts}): {e}. "
                f"Retrying in {delay:.0f}s …"
            )
            if _stop_event.wait(timeout=delay):
                return False
    return False


def run_worker():
    """Main worker entry point."""
    global _scheduler
    lock_acquired = False
    exit_code = 0

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
    configured_interval = os.getenv(
        "INGESTION_SCHEDULER_MINUTES",
        os.getenv("NEWS_FETCH_INTERVAL_MINUTES", "15"),
    )
    logger.info(f"Fetch interval configured: {configured_interval} minutes")
    logger.info(f"Fetch interval effective: {_resolve_ingestion_minutes()} minutes")
    logger.info(f"Scheduler lock key: {WORKER_LOCK_KEY}")
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
        return 0

    # Try to acquire the worker lock (with retry / back-off)
    if not _acquire_lock_with_retry():
        if _stop_event.is_set():
            logger.info("Worker startup interrupted while acquiring lock")
            sys.stdout.flush()
            return 0
        logger.error("Could not acquire worker lock after retries — exiting for restart")
        sys.stdout.flush()
        return 1

    lock_acquired = True
    logger.info("Worker lock acquired successfully")
    sys.stdout.flush()

    try:
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
            if _stop_event.is_set():
                logger.info("Worker startup interrupted during scheduler initialization")
                sys.stdout.flush()
                return 0
            logger.warning(f"Scheduler init failed (attempt {attempt}/3), retrying in 10s …")
            sys.stdout.flush()
            if _stop_event.wait(timeout=10):
                logger.info("Worker startup interrupted during scheduler retry backoff")
                sys.stdout.flush()
                return 0

        if not _scheduler:
            logger.error("Failed to initialize scheduler after 3 attempts — exiting for restart")
            sys.stdout.flush()
            return 1

        logger.info("Scheduler initialized successfully")
        sys.stdout.flush()

        # Run initial fetch (catch ALL errors so it never kills the worker)
        logger.info("Running initial news fetch...")
        sys.stdout.flush()
        try:
            fetch_and_process_news()
            logger.info("Initial fetch completed")
        except Exception as e:
            logger.error(f"Initial fetch failed (non-fatal): {e}\n" + traceback.format_exc())
        sys.stdout.flush()

        # Keep the worker running and refresh lock.
        # Tolerate transient Redis errors (returns None) — only exit on a
        # definitive lock-lost (returns False) or sustained unavailability.
        _MAX_REDIS_FAILURES = 5  # ~5 minutes of Redis unavailability before giving up
        _redis_failure_count = 0

        logger.info("Worker running. Press Ctrl+C to stop.")
        sys.stdout.flush()
        while not _stop_event.is_set():
            result = refresh_worker_lock()
            if result is True:
                # Healthy — reset the failure counter
                _redis_failure_count = 0
            elif result is None:
                # Transient Redis error — allow a few consecutive misses before
                # treating it as fatal (one blip should never kill the worker).
                _redis_failure_count += 1
                logger.warning(
                    f"Redis refresh failed ({_redis_failure_count}/{_MAX_REDIS_FAILURES}); "
                    "will retry next cycle"
                )
                if _redis_failure_count >= _MAX_REDIS_FAILURES:
                    logger.error(
                        f"Redis unreachable for {_redis_failure_count} consecutive cycles "
                        "— shutting down"
                    )
                    exit_code = 1
                    break
            else:
                # result is False — refresh CAS failed. Attempt a safe recovery
                # if the lock key was evicted/expired, but still exit if another
                # process definitively owns the lock.
                logger.warning("Worker lock refresh reported ownership loss; validating state")
                if _attempt_lock_reacquire():
                    _redis_failure_count = 0
                    continue
                logger.error("Lost worker lock and recovery failed — shutting down")
                exit_code = 1
                break
            _stop_event.wait(timeout=60)  # Wakes immediately on signal
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Worker shutting down…")
        _stop_event.set()  # Ensure everything knows we're stopping
        if _scheduler:
            _scheduler.shutdown(wait=False)
        if lock_acquired:
            if release_worker_lock():
                logger.info("Worker lock released")
            else:
                logger.info("Worker lock not released (already lost or Redis unavailable)")
        logger.info("Worker shutdown complete")
        sys.stdout.flush()
    return exit_code


def _idle_forever():
    """Block the process indefinitely so Render doesn't restart in a tight loop."""
    try:
        while not _stop_event.is_set():
            _stop_event.wait(timeout=3600)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    try:
        sys.exit(run_worker())
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
