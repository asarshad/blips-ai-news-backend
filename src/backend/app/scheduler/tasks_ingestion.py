"""Ingestion-related scheduled tasks."""

from __future__ import annotations

from app.core.dependencies import get_redis
from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import JobStats, log_job_start

logger = get_logger(__name__)


def fetch_and_process_news():
    """Fetch and store new articles/videos via checkpointed ingestion."""

    if not feature_flags.is_enabled("ingestion"):
        logger.info("[fetch_news] SKIPPED - ingestion feature is disabled")
        return

    stats = log_job_start("fetch_news")

    db = SessionLocal()
    try:
        _run_curation_ingestion_with_stats(db, stats)
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[fetch_news] Fatal error: {str(e)}")
        db.rollback()
    finally:
        db.close()
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
    except Exception as e:
        stats.errors.append(f"Curation ingestion: {str(e)}")
