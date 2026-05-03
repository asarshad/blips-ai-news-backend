"""Backfill scheduled task."""

from __future__ import annotations

import os

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


def run_backfill_job():
    """Backfill existing items into content_items."""

    stats = log_job_start("backfill")

    db = SessionLocal()
    try:
        from app.ingestion import create_ingestion_pipeline
        from app.services.article_image_service import repair_article_image_metadata

        pipeline = create_ingestion_pipeline(db)
        result = pipeline.run_backfill(
            hours_back=_int_env("BACKFILL_HOURS_BACK", 168),
            limit=_int_env("BACKFILL_CONTENT_LIMIT", 250),
        )
        image_repair = repair_article_image_metadata(
            db,
            lookback_days=_int_env("BACKFILL_IMAGE_REPAIR_LOOKBACK_DAYS", 7),
            limit=_int_env("BACKFILL_IMAGE_REPAIR_LIMIT", 100),
            max_seconds=_int_env("BACKFILL_IMAGE_REPAIR_MAX_SECONDS", 90),
            include_generic=True,
        )

        stats.items_processed = result.get("items_created", 0) if isinstance(result, dict) else 0
        logger.info(f"[backfill] Result: {result}")
        logger.info(f"[backfill] Article image repair: {image_repair}")

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[backfill] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
