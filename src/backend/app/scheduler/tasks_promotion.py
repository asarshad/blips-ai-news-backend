"""Scheduled task for the promotion quality gate."""

from __future__ import annotations

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def run_promotion_job() -> None:
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

    stats = log_job_start("promotion")
    db = SessionLocal()
    try:
        from app.services.promotion_service import PromotionService
        from app.services.push_service import PushNotificationService

        svc = PromotionService(db)
        result = svc.run_promotion_job()
        auto_push_results = PushNotificationService(db=db).send_auto_for_content_ids(
            result.promoted_ids,
            actor="scheduler:promotion",
        )

        stats.items_processed = result.promoted_count
        logger.info(
            "[promotion] evaluated=%d promoted=%d rescored=%d auto_push=%d errors=%d",
            result.candidates_evaluated,
            result.promoted_count,
            result.already_promoted_rescored,
            len([entry for entry in auto_push_results if not entry.skipped]),
            len(result.errors),
        )
        if result.errors:
            stats.errors.extend(result.errors)

    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[promotion] Fatal error: %s", exc)
        db.rollback()
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
