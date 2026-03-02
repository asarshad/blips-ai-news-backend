"""Scheduled cleanup tasks for data retention compliance.

Runs once per UTC day via APScheduler.  Uses a Redis lease to prevent
parallel runs across replicas.

Retention periods are configurable via env vars — see config.py:
  - RETAIN_CONTENT_DAYS (90)
  - RETAIN_INGESTION_PROGRESS_DAYS (14)
  - RETAIN_EVENTS_DAYS (30)
  - RETAIN_CONVERSATIONS_DAYS (30)
  - RETAIN_USAGE_DAYS (90)
  - RETAIN_EDITORIAL_DAYS (180)
"""

from datetime import datetime

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start
from app.services.retention_service import run_retention_cleanup

logger = get_logger(__name__)

# Redis lock key & TTL for cleanup job
CLEANUP_LOCK_KEY = "blips:cleanup_lock"
CLEANUP_LOCK_TTL = 600  # 10 minutes — generous ceiling for the job
_cleanup_lock_token: str = ""


def _acquire_cleanup_lock() -> bool:
    """Acquire a Redis NX lock so only one replica runs cleanup."""
    global _cleanup_lock_token
    try:
        import uuid

        from app.core.dependencies import get_redis
        token = str(uuid.uuid4())
        r = get_redis()
        acquired = bool(r.set(CLEANUP_LOCK_KEY, token, nx=True, ex=CLEANUP_LOCK_TTL))
        if acquired:
            _cleanup_lock_token = token
        return acquired
    except Exception as e:
        logger.warning(f"[data_cleanup] Redis lock unavailable, proceeding anyway: {e}")
        return True  # fail-open: still run if Redis is down


def _release_cleanup_lock() -> None:
    """Release the cleanup lock via compare-and-delete (CAS).

    Prevents releasing another instance's lock if our TTL expired.
    """
    global _cleanup_lock_token
    if not _cleanup_lock_token:
        return
    try:
        from app.core.dependencies import get_redis
        r = get_redis()
        lua = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) "
            "else return 0 end"
        )
        r.eval(lua, 1, CLEANUP_LOCK_KEY, _cleanup_lock_token)
    except Exception:
        pass  # TTL will auto-expire
    finally:
        _cleanup_lock_token = ""


def run_data_cleanup_job() -> dict:
    """
    Purge records older than retention thresholds.

    Returns the cleanup result dict (useful for admin endpoint).
    """
    stats = log_job_start("data_cleanup")

    if not _acquire_cleanup_lock():
        logger.info("[data_cleanup] Skipped — another instance holds the lock")
        stats.complete()
        stats.log_summary()
        return {"skipped": True, "reason": "lock_held"}

    db = SessionLocal()
    try:
        result = run_retention_cleanup(db)
        stats.items_processed = result.total_deleted
        if result.errors:
            stats.errors.extend(result.errors)

        # Record last run timestamp in Redis for observability
        try:
            from app.core.dependencies import get_redis
            r = get_redis()
            r.setex("blips:cleanup:last_run_at", 86400 * 2, datetime.utcnow().isoformat())
        except Exception:
            pass

        return result.to_dict()

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[data_cleanup] Fatal error: {e}")
        return {"error": str(e)}

    finally:
        db.close()
        _release_cleanup_lock()
        stats.complete()
        stats.log_summary()
