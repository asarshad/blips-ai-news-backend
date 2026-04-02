"""Scheduled task for the promotion quality gate."""

from __future__ import annotations

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start
from app.scheduler.runtime import (
    FETCH_NEWS_INLINE_PROMOTION,
    FETCH_NEWS_JOB,
    PROMOTION_JOB,
    get_followup_cooldown_seconds,
    is_job_active,
    log_memory_snapshot,
    mark_job_finished,
    mark_job_started,
    succeeded_within,
)

logger = get_logger(__name__)


def run_promotion_job(*, trigger: str = "scheduled") -> None:
    """Score CANDIDATE items and promote top-N to PROMOTED.

    Cadence: every 30 minutes (runs right after signal ingestion completes
    and also after each regular ingestion run via ``fetch_and_process_news``).

    Workflow:
    1. Fetch all CANDIDATE items from the last 48 hours.
    2. Score each using PromotionService (source quality, cluster hotness,
       recency, clickbait penalty, duplicate density penalty).
    3. Promote the top-N above the minimum threshold to PROMOTED.
    4. Refresh promotion_score on already-PROMOTED items for observability.
    """
    if not feature_flags.is_enabled("promotion"):
        logger.info("[promotion] SKIPPED – 'promotion' feature flag disabled")
        return

    if trigger == "scheduled":
        if is_job_active(FETCH_NEWS_JOB):
            logger.info("[promotion] SKIPPED - fetch_news is still active")
            return
        if succeeded_within(
            FETCH_NEWS_INLINE_PROMOTION,
            within_seconds=get_followup_cooldown_seconds(),
        ):
            logger.info("[promotion] SKIPPED - recent inline promotion already ran")
            return

    stats = log_job_start("promotion")
    job_key = FETCH_NEWS_INLINE_PROMOTION if trigger == "fetch_news" else PROMOTION_JOB
    run_started_at = None
    run_success = False
    db = None
    try:
        run_started_at = mark_job_started(job_key)
        log_memory_snapshot(logger, f"{job_key}:start")
        db = SessionLocal()
        from app.scheduler.tasks_content_events import run_content_event_dispatch_job
        from app.services.promotion_service import PromotionService

        svc = PromotionService(db)
        result = svc.run_promotion_job()
        run_content_event_dispatch_job()

        stats.items_processed = result.promoted_count
        logger.info(
            "[promotion] evaluated=%d promoted=%d rescored=%d errors=%d",
            result.candidates_evaluated,
            result.promoted_count,
            result.already_promoted_rescored,
            len(result.errors),
        )
        if result.errors:
            stats.errors.extend(result.errors)
        run_success = not stats.errors

    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[promotion] Fatal error: %s", exc)
        if db is not None:
            db.rollback()
    finally:
        log_memory_snapshot(logger, f"{job_key}:finished")
        if run_started_at is not None:
            mark_job_finished(job_key, run_started_at, success=run_success)
        if db is not None:
            db.close()
        stats.complete()
        stats.log_summary()
