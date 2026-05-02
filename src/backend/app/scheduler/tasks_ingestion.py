"""Ingestion-related scheduled tasks."""

from __future__ import annotations

from app.core.dependencies import get_redis
from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import JobStats, log_job_start
from app.scheduler.runtime import (
    FETCH_NEWS_JOB,
    current_rss_mb,
    log_memory_snapshot,
    mark_job_finished,
    memory_hard_limit_mb,
    memory_over_hard_limit,
    try_mark_job_started,
)
from app.services.video_content_policy import youtube_discovery_enabled

logger = get_logger(__name__)


def fetch_and_process_news():
    """Fetch new content and queue it for event-driven processing.

    Runs checkpointed ingestion which inserts articles/videos (ai_processed=False)
    and queues outbox events for promotion and AI summarization. The content event
    worker threads drain those events in near-real-time.
    """

    if not feature_flags.is_enabled("ingestion"):
        logger.info("[fetch_news] SKIPPED - ingestion feature is disabled")
        return
    if memory_over_hard_limit():
        logger.warning(
            "[fetch_news] SKIPPED - worker memory is above hard limit rss_mb=%s hard_limit_mb=%s",
            current_rss_mb(),
            memory_hard_limit_mb(),
        )
        return

    stats = log_job_start("fetch_news")
    run_started_at = None
    fetch_success = False
    db = None
    try:
        run_started_at = try_mark_job_started(
            FETCH_NEWS_JOB,
            unless_active=(FETCH_NEWS_JOB,),
        )
        if run_started_at is None:
            logger.info("[fetch_news] SKIPPED - fetch_news is already active")
            return
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
    finally:
        log_memory_snapshot(logger, "fetch_news:finished")
        if run_started_at is not None:
            mark_job_finished(FETCH_NEWS_JOB, run_started_at, success=fetch_success)
        stats.complete()
        stats.log_summary()


def _run_curation_ingestion_with_stats(db, stats: JobStats):
    try:
        from app.ingestion.checkpointing import run_checkpointed_ingestion

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

        logger.info("[fetch_news] Follow-up work queued via outbox; lanes will drain asynchronously")
    except Exception as e:
        stats.errors.append(f"Curation ingestion: {str(e)}")
