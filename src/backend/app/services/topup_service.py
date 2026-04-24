"""
Inventory Top-Up Trigger Service.

Provides non-blocking ingestion kick mechanism:
- On service startup if inventory is low
- On API requests when cached health shows low inventory
- Uses Redis lock to prevent multiple concurrent top-ups

This service does NOT block API responses - it queues background work.
"""

import os
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.content_repo import ContentItemRepository
from app.services.freshness_metrics_service import (
    record_topup_completed,
    record_topup_triggered,
)
from app.services.inventory_service import (
    InventoryHealth,
    get_cached_inventory_health,
    invalidate_health_cache,
)
from app.services.tiered_feed_service import invalidate_tiered_feed_cache
from app.services.video_content_policy import youtube_discovery_enabled

logger = get_logger(__name__)

# Module-level state for background top-up
_topup_lock = threading.Lock()
_topup_in_progress = False
_last_topup_trigger: Optional[datetime] = None


def _article_target_pending(db: Session) -> bool:
    try:
        from app.ingestion.time import get_ingestion_day

        day = get_ingestion_day()
        content_repo = ContentItemRepository(db)
        supply = content_repo.get_article_supply_counts_on_ingestion_day(day)
        if supply["ready"] >= settings.DAILY_TARGET_ARTICLES:
            return False
        return True
    except Exception as exc:
        logger.warning("Unable to evaluate pending article target during top-up: %s", exc)
        return False


def _background_topup_enabled_for_process() -> bool:
    """Return whether this process should launch top-up background work.

    Production runs a dedicated worker service with ``SCHEDULER_ENABLED=true``
    and a web service with ``SCHEDULER_ENABLED=false``. Letting feed requests
    in the web service trigger ingestion causes the API process to execute the
    same memory-heavy work as the worker, which can push the web dyno over its
    memory limit.
    """
    return os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"


def _get_redis_client():
    """Get Redis client using shared connection pool."""
    try:
        from app.core.dependencies import get_redis

        return get_redis()
    except Exception as e:
        logger.warning(f"Redis not available for top-up lock: {e}")
        return None


def _acquire_topup_lock() -> bool:
    """
    Acquire distributed lock for top-up operation.

    Returns:
        True if lock acquired, False if another top-up is in progress
    """
    redis_client = _get_redis_client()
    if redis_client:
        try:
            # Try to set NX (only if not exists) with TTL
            lock_key = "blips:topup_lock"
            ttl = settings.TOPUP_LOCK_TTL_SECONDS
            acquired = redis_client.set(lock_key, "1", nx=True, ex=ttl)
            return bool(acquired)
        except Exception as e:
            logger.warning(f"Redis lock failed, using local lock: {e}")

    # Fallback to local lock
    global _topup_in_progress
    with _topup_lock:
        if _topup_in_progress:
            return False
        _topup_in_progress = True
        return True


def _release_topup_lock():
    """Release the distributed top-up lock."""
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_client.delete("blips:topup_lock")
        except Exception as e:
            logger.warning(f"Redis lock release failed: {e}")

    global _topup_in_progress
    with _topup_lock:
        _topup_in_progress = False


def should_trigger_topup(health: InventoryHealth) -> bool:
    """
    Determine if top-up should be triggered based on health.

    Args:
        health: Current inventory health

    Returns:
        True if any surface is below thresholds
    """
    return health.needs_topup


def _run_topup_followups() -> None:
    """Advance freshly ingested content through clustering; promotion and AI drain via outbox."""
    from app.scheduler.tasks_curation import run_clustering_job

    logger.info("Running top-up follow-up pipeline")
    run_clustering_job(trigger="topup")
    logger.info("Top-up clustering complete; promotion and AI requests queued via outbox")


def trigger_topup_async(db_factory, priority_surfaces: list = None):
    """
    Trigger top-up in background thread (non-blocking).

    Args:
        db_factory: Factory function to create database sessions
        priority_surfaces: List of surfaces to prioritize
    """
    global _last_topup_trigger

    if not _background_topup_enabled_for_process():
        logger.debug(
            "Skipping async top-up because background scheduling is disabled for this process"
        )
        return

    # Check cooldown (don't trigger too frequently)
    now = datetime.utcnow()
    if _last_topup_trigger:
        cooldown = 30  # seconds
        elapsed = (now - _last_topup_trigger).total_seconds()
        if elapsed < cooldown:
            logger.debug(f"Top-up cooldown: {cooldown - elapsed:.0f}s remaining")
            return

    if not _acquire_topup_lock():
        logger.debug("Top-up already in progress, skipping")
        return

    _last_topup_trigger = now
    logger.info(f"Triggering async top-up for surfaces: {priority_surfaces or 'all'}")
    record_topup_triggered(priority_surfaces)

    # Start background thread
    thread = threading.Thread(
        target=_run_topup,
        args=(db_factory, priority_surfaces),
        daemon=True,
    )
    thread.start()


def _run_topup(db_factory, priority_surfaces: list = None):
    """
    Run top-up ingestion in background.

    This is the actual ingestion work that runs in a background thread.
    """
    try:
        logger.info("Starting background top-up ingestion")
        start = datetime.utcnow()
        max_runtime = settings.TOPUP_MAX_RUNTIME_SECONDS

        # Import here to avoid circular imports
        from app.core.dependencies import get_redis
        from app.ingestion.checkpointing import run_checkpointed_ingestion

        # Create a new database session for background work
        db = db_factory()
        try:
            # Get Redis client if available
            redis_client = None
            try:
                redis_client = get_redis()
            except Exception:
                pass

            # Run ingestion cycles until satisfied or timeout
            cycles = 0
            max_cycles = 5

            while cycles < max_cycles:
                elapsed = (datetime.utcnow() - start).total_seconds()
                if elapsed > max_runtime:
                    logger.info(f"Top-up max runtime reached ({max_runtime}s)")
                    break

                # Check if we're satisfied now
                health = get_cached_inventory_health(db, force_refresh=True)
                article_target_pending = _article_target_pending(db)
                if not health.needs_topup and not article_target_pending:
                    logger.info("Top-up complete: inventory thresholds met")
                    break

                # Run a single ingestion pass using checkpointed ingestion
                result = run_checkpointed_ingestion(db, redis_client=redis_client)
                logger.info(f"Top-up cycle {cycles + 1}: {result}")
                if youtube_discovery_enabled():
                    from app.ingestion.service import run_video_discovery_ingestion

                    discovery_result = run_video_discovery_ingestion(db)
                    logger.info(f"Top-up discovery cycle {cycles + 1}: {discovery_result}")
                _run_topup_followups()
                cycles += 1

            # Invalidate caches after top-up
            invalidate_health_cache()
            invalidate_tiered_feed_cache()

            elapsed = (datetime.utcnow() - start).total_seconds()
            logger.info(f"Top-up finished: {cycles} cycles in {elapsed:.1f}s")
            record_topup_completed(
                priority_surfaces=priority_surfaces,
                duration_seconds=elapsed,
                cycles=cycles,
            )

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Top-up failed: {e}", exc_info=True)
    finally:
        _release_topup_lock()


def check_and_trigger_topup(db: Session, db_factory) -> bool:
    """
    Check inventory health and trigger top-up if needed.

    This is called from API endpoints and startup.
    It does NOT block - returns immediately after potentially queuing work.

    Args:
        db: Database session for health check
        db_factory: Factory to create new sessions for background work

    Returns:
        True if top-up was triggered, False otherwise
    """
    if not _background_topup_enabled_for_process():
        logger.debug(
            "Skipping request-triggered top-up because background scheduling "
            "is disabled for this process"
        )
        return False

    try:
        health = get_cached_inventory_health(db)
        article_target_pending = _article_target_pending(db)

        if should_trigger_topup(health) or article_target_pending:
            trigger_topup_async(db_factory, [s.value for s in health.topup_priority])
            return True

        return False
    except Exception as e:
        logger.warning(f"Failed to check/trigger top-up: {e}")
        return False


def startup_inventory_check(db_factory):
    """
    Check inventory on service startup and trigger top-up if needed.

    This should be called from main.py during app startup.
    """
    logger.info("Running startup inventory check")

    try:
        db = db_factory()
        try:
            health = get_cached_inventory_health(db, force_refresh=True)

            logger.info(
                f"Startup inventory health: healthy={health.is_healthy}, "
                f"needs_topup={health.needs_topup}, "
                f"priority={[s.value for s in health.topup_priority]}"
            )

            if health.needs_topup or _article_target_pending(db):
                trigger_topup_async(db_factory, [s.value for s in health.topup_priority])

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Startup inventory check failed: {e}", exc_info=True)
