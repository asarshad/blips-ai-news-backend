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
WORKER_LOCK_REFRESH_SECONDS = max(
    5,
    min(int(os.getenv("SCHEDULER_LOCK_REFRESH_SECONDS", "30")), max(5, WORKER_LOCK_TTL - 1)),
)
LOCK_REACQUIRE_ATTEMPTS = 3
LOCK_REACQUIRE_DELAY_SECONDS = 5.0
DEFAULT_CONTENT_EVENT_WORKER_SPECS = (
    "content.promotion_eval.requested;"
    "content.ai_summary.requested;"
    "article.image_verification.requested,content.ready,content.unready"
)


def _request_worker_stop() -> None:
    """Propagate graceful-stop intent to worker and ingestion helpers."""
    _stop_event.set()
    try:
        from app.ingestion.checkpointing import STOP_EVENT

        STOP_EVENT.set()
    except Exception:
        pass


def signal_handler(signum, _frame):
    """Handle shutdown signals gracefully.

    Sets the shared stop event so in-flight ingestion / ThreadPoolExecutors
    finish their current batch and stop accepting new futures *before* we
    tear down the APScheduler.
    """
    logger.info(f"Received signal {signum}, requesting graceful shutdown…")
    _request_worker_stop()


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


def _maintain_worker_lock(
    stop_event: threading.Event,
    *,
    refresh_interval_seconds: float,
    phase: str,
) -> int:
    """Refresh the distributed worker lock until stopped or a fatal failure occurs."""
    max_redis_failures = 5
    redis_failure_count = 0

    while not _stop_event.is_set() and not stop_event.is_set():
        if stop_event.wait(timeout=refresh_interval_seconds):
            return 0
        if _stop_event.is_set():
            return 0

        result = refresh_worker_lock()
        if result is True:
            redis_failure_count = 0
            continue

        if result is None:
            redis_failure_count += 1
            logger.warning(
                "Redis refresh failed during %s (%s/%s); will retry next cycle",
                phase,
                redis_failure_count,
                max_redis_failures,
            )
            if redis_failure_count >= max_redis_failures:
                logger.error(
                    "Redis unreachable for %s consecutive lock refresh cycles during %s — shutting down",
                    redis_failure_count,
                    phase,
                )
                return 1
            continue

        logger.warning("Worker lock refresh reported ownership loss during %s; validating state", phase)
        if _attempt_lock_reacquire():
            redis_failure_count = 0
            continue
        logger.error("Lost worker lock during %s and recovery failed — shutting down", phase)
        return 1

    return 0


def _content_event_threads_enabled() -> bool:
    return os.getenv("CONTENT_EVENT_THREADS_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def _resolve_content_event_worker_specs() -> tuple[tuple[str, ...], ...]:
    raw = os.getenv("CONTENT_EVENT_WORKER_SPECS", DEFAULT_CONTENT_EVENT_WORKER_SPECS)
    groups = [chunk.strip() for chunk in raw.split(";") if chunk.strip()]
    parsed: list[tuple[str, ...]] = []
    for group in groups:
        event_types = tuple(part.strip() for part in group.split(",") if part.strip())
        if event_types:
            parsed.append(event_types)
    return tuple(parsed)


def _start_content_event_worker_threads(stop_event: threading.Event) -> list[threading.Thread]:
    if not _content_event_threads_enabled():
        return []

    from app.content_event_worker import run_content_event_worker

    batch_size = int(os.getenv("CONTENT_EVENT_BATCH_SIZE", "50"))
    poll_seconds = float(os.getenv("CONTENT_EVENT_POLL_SECONDS", "1.0"))
    threads: list[threading.Thread] = []
    for idx, event_types in enumerate(_resolve_content_event_worker_specs(), start=1):
        thread = threading.Thread(
            target=run_content_event_worker,
            kwargs={
                "event_types": event_types,
                "batch_size": batch_size,
                "poll_seconds": poll_seconds,
                "stop_event": stop_event,
            },
            daemon=True,
            name=f"content-event-worker-{idx}",
        )
        thread.start()
        logger.info(
            "Started content event worker thread %s for event_types=%s",
            thread.name,
            ",".join(event_types),
        )
        threads.append(thread)
    return threads


def run_worker():
    """Main worker entry point."""
    global _scheduler
    lock_acquired = False
    exit_code = 0
    content_event_threads: list[threading.Thread] = []

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
        startup_lock_stop_event = threading.Event()
        startup_lock_failures: list[int] = []

        def _maintain_lock_during_startup_fetch() -> None:
            exit_status = _maintain_worker_lock(
                startup_lock_stop_event,
                refresh_interval_seconds=float(WORKER_LOCK_REFRESH_SECONDS),
                phase="startup fetch",
            )
            if exit_status != 0:
                startup_lock_failures.append(exit_status)
                _request_worker_stop()

        startup_lock_thread = threading.Thread(
            target=_maintain_lock_during_startup_fetch,
            daemon=True,
            name="worker-startup-lock-refresher",
        )
        startup_lock_thread.start()

        logger.info("Running initial news fetch...")
        sys.stdout.flush()
        try:
            fetch_and_process_news()
            logger.info("Initial fetch completed")
        except Exception as e:
            logger.error(f"Initial fetch failed (non-fatal): {e}\n" + traceback.format_exc())
        finally:
            startup_lock_stop_event.set()
            startup_lock_thread.join()
        sys.stdout.flush()
        if startup_lock_failures:
            logger.error("Worker lock maintenance failed during startup fetch — exiting for restart")
            sys.stdout.flush()
            return 1

        content_event_threads = _start_content_event_worker_threads(_stop_event)
        # Keep the worker running and refresh lock.
        logger.info("Worker running. Press Ctrl+C to stop.")
        sys.stdout.flush()
        exit_code = _maintain_worker_lock(
            _stop_event,
            refresh_interval_seconds=float(WORKER_LOCK_REFRESH_SECONDS),
            phase="steady-state operation",
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Worker shutting down…")
        _request_worker_stop()
        if _scheduler:
            _scheduler.shutdown(wait=False)
        for thread in content_event_threads:
            thread.join(timeout=5)
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
