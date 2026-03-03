"""Backfill scheduled task."""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def run_backfill_job():
    """Backfill existing items into content_items."""

    stats = log_job_start("backfill")

    db = SessionLocal()
    try:
        from app.ingestion import create_ingestion_pipeline

        pipeline = create_ingestion_pipeline(db)
        result = pipeline.run_backfill(hours_back=168, limit=1000)  # 7 days

        stats.items_processed = result.get("items_created", 0) if isinstance(result, dict) else 0
        logger.info(f"[backfill] Result: {result}")

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[backfill] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
