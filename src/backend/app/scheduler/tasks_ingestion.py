"""Ingestion-related scheduled tasks."""

from __future__ import annotations

import os

from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import JobStats, log_job_start
from app.scheduler.runtime import FETCH_NEWS_JOB, log_memory_snapshot, mark_job_finished, mark_job_started
from app.services.video_content_policy import youtube_discovery_enabled

logger = get_logger(__name__)


def _resolve_immediate_ai_summary_max_items() -> int:
    """Resolve the post-ingestion AI batch size using the article-priority ceiling by default."""
    raw = os.getenv(
        "IMMEDIATE_AI_SUMMARY_MAX_ITEMS",
        str(settings.ARTICLE_AI_PRIORITY_MAX_ITEMS_PER_RUN),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid IMMEDIATE_AI_SUMMARY_MAX_ITEMS=%r; defaulting to %s",
            raw,
            settings.ARTICLE_AI_PRIORITY_MAX_ITEMS_PER_RUN,
        )
        return int(settings.ARTICLE_AI_PRIORITY_MAX_ITEMS_PER_RUN)


def run_immediate_ai_summaries(*, trigger: str = "fetch_news", include_maintenance: bool = False):
    """Run the immediate AI summarization pass used after ingestion-like flows."""
    from app.scheduler.tasks_ai_retry import process_ai_summaries

    immediate_limit = _resolve_immediate_ai_summary_max_items()
    logger.info(
        "[%s] Running immediate AI summarization (max_items=%s, maintenance=%s)…",
        trigger,
        immediate_limit,
        str(include_maintenance).lower(),
    )
    process_ai_summaries(
        max_items=immediate_limit,
        include_maintenance=include_maintenance,
        trigger=trigger,
    )


def fetch_and_process_news():
    """Fetch new content then immediately run AI summarization.

    Two phases:
    1. Checkpointed ingestion — bulk-inserts articles/videos (ai_processed=False).
    2. AI processing — summarises every unprocessed item so the feed is
       populated without waiting for the 15-min retry scheduler.
    """

    if not feature_flags.is_enabled("ingestion"):
        logger.info("[fetch_news] SKIPPED - ingestion feature is disabled")
        return

    stats = log_job_start("fetch_news")
    run_started_at = None
    fetch_success = False
    db = None
    try:
        run_started_at = mark_job_started(FETCH_NEWS_JOB)
        log_memory_snapshot(logger, "fetch_news:start")

        db = SessionLocal()
        try:
            _run_curation_ingestion_with_stats(db, stats)
            fetch_success = not stats.errors
        except Exception as e:
            stats.errors.append(str(e))
            logger.error(f"[fetch_news] Fatal error: {str(e)}")
            db.rollback()
            fetch_success = False
        finally:
            if db is not None:
                db.close()
            log_memory_snapshot(logger, "fetch_news:after_curation")

        # Phase 2: immediately summarise newly-ingested items so they appear
        # in the feed right away instead of waiting for the next ai_retry tick.
        try:
            run_immediate_ai_summaries(trigger="fetch_news", include_maintenance=False)
            logger.info("[fetch_news] AI summarization complete")
        except Exception as e:
            # Non-fatal — the periodic ai_retry job will pick them up later.
            logger.warning(f"[fetch_news] Immediate AI summarization failed (non-fatal): {e}")
            fetch_success = False
    finally:
        log_memory_snapshot(logger, "fetch_news:finished")
        if run_started_at is not None:
            mark_job_finished(FETCH_NEWS_JOB, run_started_at, success=fetch_success)
        stats.complete()
        stats.log_summary()


def _run_curation_ingestion_with_stats(db, stats: JobStats):
    try:
        from app.ingestion.checkpointing import run_checkpointed_ingestion
        from app.scheduler.tasks_curation import run_clustering_job
        from app.scheduler.tasks_promotion import run_promotion_job

        redis_client = None
        try:
            redis_client = get_redis()
        except Exception:
            redis_client = None

        result = run_checkpointed_ingestion(db, redis_client=redis_client)
        logger.info(f"[fetch_news] Curation ingestion: {result}")

        if youtube_discovery_enabled():
            from app.ingestion.service import run_video_discovery_ingestion

            discovery_result = run_video_discovery_ingestion(db)
            logger.info(f"[fetch_news] Video discovery ingestion: {discovery_result}")
            stats.items_processed += int(discovery_result.get("videos_ingested", 0)) + int(
                discovery_result.get("reels_ingested", 0)
            )
            if int(discovery_result.get("errors", 0)) > 0:
                stats.errors.append(f"Video discovery ingestion: {discovery_result}")
        else:
            logger.info("[fetch_news] Curated-only mode active; discovery ingestion skipped")

        logger.info("[fetch_news] Running clustering before promotion")
        run_clustering_job(trigger="fetch_news")
        logger.info("[fetch_news] Running promotion immediately after ingestion")
        run_promotion_job(trigger="fetch_news")
    except Exception as e:
        stats.errors.append(f"Curation ingestion: {str(e)}")
