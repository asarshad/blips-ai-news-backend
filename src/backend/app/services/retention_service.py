"""
Data retention service for automated database cleanup.

Implements safe, idempotent retention policies:
- content_items:         RETAIN_CONTENT_DAYS (90d default), editorial items protected
- ingestion_progress:    RETAIN_INGESTION_PROGRESS_DAYS (14d)
- ingestion_budgets:     RETAIN_INGESTION_PROGRESS_DAYS (14d) — same lifecycle
- source_daily_stats:    RETAIN_INGESTION_PROGRESS_DAYS (14d) — same lifecycle
- editorial_actions:     RETAIN_EDITORIAL_DAYS (180d)
- interaction_events:    RETAIN_EVENTS_DAYS (30d)
- conversations:         RETAIN_CONVERSATIONS_DAYS (30d)
- usage:                 RETAIN_USAGE_DAYS (90d)

All operations are:
- Batch-oriented (limit per pass to avoid long locks)
- Logged with row counts per table
- Idempotent (safe to re-run)
- Protected: editorial/manual items are excluded from content cleanup
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Maximum rows to delete per table per run to limit lock duration
MAX_DELETE_BATCH = 5000


@dataclass
class CleanupResult:
    """Results from a retention cleanup run."""
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    content_items_deleted: int = 0
    ingestion_progress_deleted: int = 0
    ingestion_budgets_deleted: int = 0
    source_daily_stats_deleted: int = 0
    editorial_actions_deleted: int = 0
    interaction_events_deleted: int = 0
    conversations_deleted: int = 0
    usage_deleted: int = 0
    errors: list = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return (
            self.content_items_deleted
            + self.ingestion_progress_deleted
            + self.ingestion_budgets_deleted
            + self.source_daily_stats_deleted
            + self.editorial_actions_deleted
            + self.interaction_events_deleted
            + self.conversations_deleted
            + self.usage_deleted
        )

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or datetime.utcnow()
        return (end - self.started_at).total_seconds()

    def complete(self):
        self.ended_at = datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_deleted": self.total_deleted,
            "tables": {
                "content_items": self.content_items_deleted,
                "ingestion_progress": self.ingestion_progress_deleted,
                "ingestion_budgets": self.ingestion_budgets_deleted,
                "source_daily_stats": self.source_daily_stats_deleted,
                "editorial_actions": self.editorial_actions_deleted,
                "interaction_events": self.interaction_events_deleted,
                "conversations": self.conversations_deleted,
                "usage": self.usage_deleted,
            },
            "errors": self.errors[:10],
        }


def run_retention_cleanup(db: Session) -> CleanupResult:
    """
    Execute retention cleanup across all tables.

    Safe to call from scheduler or admin endpoint.
    Each table is cleaned independently so a failure in one
    does not block the others.
    """
    result = CleanupResult()
    now = datetime.utcnow()

    # --- content_items ---
    _cleanup_content_items(db, now, result)

    # --- ingestion_progress ---
    _cleanup_table(
        db, now, result,
        table="ingestion_progress",
        column="created_at",
        days=settings.RETAIN_INGESTION_PROGRESS_DAYS,
        attr="ingestion_progress_deleted",
    )

    # --- ingestion_budgets (keyed by day column, not created_at) ---
    _cleanup_table(
        db, now, result,
        table="ingestion_budgets",
        column="day",
        days=settings.RETAIN_INGESTION_PROGRESS_DAYS,
        attr="ingestion_budgets_deleted",
        is_date=True,
    )

    # --- source_daily_stats (keyed by day column) ---
    _cleanup_table(
        db, now, result,
        table="source_daily_stats",
        column="day",
        days=settings.RETAIN_INGESTION_PROGRESS_DAYS,
        attr="source_daily_stats_deleted",
        is_date=True,
    )

    # --- editorial_actions ---
    _cleanup_table(
        db, now, result,
        table="editorial_actions",
        column="created_at",
        days=settings.RETAIN_EDITORIAL_DAYS,
        attr="editorial_actions_deleted",
    )

    # --- interaction_events ---
    _cleanup_table(
        db, now, result,
        table="interaction_events",
        column="created_at",
        days=settings.RETAIN_EVENTS_DAYS,
        attr="interaction_events_deleted",
    )

    # --- conversations ---
    _cleanup_table(
        db, now, result,
        table="conversations",
        column="timestamp",
        days=settings.RETAIN_CONVERSATIONS_DAYS,
        attr="conversations_deleted",
    )

    # --- usage ---
    _cleanup_table(
        db, now, result,
        table="usage",
        column="timestamp",
        days=settings.RETAIN_USAGE_DAYS,
        attr="usage_deleted",
    )

    result.complete()
    logger.info(
        f"[retention] Cleanup complete: {result.total_deleted} rows deleted "
        f"in {result.duration_seconds:.1f}s"
    )
    return result


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _cleanup_content_items(db: Session, now: datetime, result: CleanupResult) -> None:
    """
    Delete old content_items, protecting editorial items.

    Protected items (skipped):
    - editorial_boost > 0
    - manual_added = true
    """
    cutoff = now - timedelta(days=settings.RETAIN_CONTENT_DAYS)
    try:
        # Use raw SQL for efficient batch delete with protection clause
        stmt = text("""
            DELETE FROM content_items
            WHERE id IN (
                SELECT id FROM content_items
                WHERE created_at < :cutoff
                  AND (editorial_boost = 0 OR editorial_boost IS NULL)
                  AND (manual_added = false OR manual_added IS NULL)
                ORDER BY created_at ASC
                LIMIT :batch_limit
            )
        """)
        res = db.execute(stmt, {"cutoff": cutoff, "batch_limit": MAX_DELETE_BATCH})
        count = res.rowcount
        db.commit()
        result.content_items_deleted = count
        logger.info(
            f"[retention] content_items: deleted {count} rows "
            f"older than {cutoff.date()} (editorial items protected)"
        )
    except Exception as e:
        db.rollback()
        result.errors.append(f"content_items: {e}")
        logger.error(f"[retention] content_items cleanup failed: {e}")


def _cleanup_table(
    db: Session,
    now: datetime,
    result: CleanupResult,
    *,
    table: str,
    column: str,
    days: int,
    attr: str,
    is_date: bool = False,
) -> None:
    """Generic batch cleanup for a table with a timestamp/date column."""
    cutoff = now - timedelta(days=days)
    cutoff_value = cutoff.date() if is_date else cutoff
    try:
        stmt = text(f"""
            DELETE FROM {table}
            WHERE {column} < :cutoff
        """)
        res = db.execute(stmt, {"cutoff": cutoff_value})
        count = res.rowcount
        db.commit()
        setattr(result, attr, count)
        logger.info(f"[retention] {table}: deleted {count} rows older than {days}d")
    except Exception as e:
        db.rollback()
        result.errors.append(f"{table}: {e}")
        logger.error(f"[retention] {table} cleanup failed: {e}")
