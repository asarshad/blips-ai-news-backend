"""Dedicated scheduled job for article image verification.

Historically, image verification ran as a side-effect of ``ai_retry``'s
maintenance phase. That coupling meant image backlog only drained once
every ~15 minutes and had to wait behind LLM summarization on the same
single worker. For a non-trivial share of articles the image pipeline is
the dominant blocker on reaching ``READY``, so this job runs on its own
fast cadence and is independent of LLM work.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start
from app.scheduler.runtime import (
    FETCH_NEWS_JOB,
    is_job_active,
    log_memory_snapshot,
    mark_job_finished,
    mark_job_started,
)
from app.services.article_image_service import repair_article_image_metadata

logger = get_logger(__name__)

ARTICLE_IMAGE_VERIFICATION_JOB = "article_image_verification"


def run_article_image_verification_job() -> None:
    """Run the dedicated article image verification pass.

    Skipped transparently while a ``fetch_news`` cycle is in flight — the
    ingestion path already triggers verification for newly-promoted rows
    and we want to avoid two image-heavy DB workloads overlapping on the
    single worker process.
    """
    if is_job_active(FETCH_NEWS_JOB):
        logger.info(
            "[%s] fetch_news is active; skipping image verification tick",
            ARTICLE_IMAGE_VERIFICATION_JOB,
        )
        return

    stats = log_job_start(ARTICLE_IMAGE_VERIFICATION_JOB)
    run_started_at = None
    run_success = False
    db = None
    try:
        run_started_at = mark_job_started(ARTICLE_IMAGE_VERIFICATION_JOB)
        log_memory_snapshot(logger, f"{ARTICLE_IMAGE_VERIFICATION_JOB}:start")
        db = SessionLocal()
        result = repair_article_image_metadata(
            db,
            lookback_days=settings.ARTICLE_IMAGE_REPAIR_LOOKBACK_DAYS,
            limit=settings.ARTICLE_IMAGE_REPAIR_LIMIT,
            include_generic=True,
            promoted_only=True,
            readiness_reasons=(
                "missing_article_image",
                "awaiting_article_image_verification",
            ),
        )
        stats.items_processed = int(result.get("updated", 0))
        stats.items_skipped = max(
            0, int(result.get("scanned", 0)) - int(result.get("updated", 0))
        )
        stats.items_failed = int(result.get("failures", 0))
        logger.info("[%s] %s", ARTICLE_IMAGE_VERIFICATION_JOB, result)
        run_success = stats.items_failed == 0
    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[%s] Fatal error: %s", ARTICLE_IMAGE_VERIFICATION_JOB, exc)
    finally:
        log_memory_snapshot(logger, f"{ARTICLE_IMAGE_VERIFICATION_JOB}:finished")
        if run_started_at is not None:
            mark_job_finished(
                ARTICLE_IMAGE_VERIFICATION_JOB, run_started_at, success=run_success
            )
        if db is not None:
            db.close()
        stats.complete()
        stats.log_summary()
