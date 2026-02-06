"""Repository for ingestion progress rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from itertools import zip_longest
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.ingestion_progress import IngestionProgress


def _interleave_by_source_type(rows: List[IngestionProgress]) -> List[IngestionProgress]:
    """Interleave rows by source_type for fair round-robin processing.
    
    This ensures rss, youtube_video, and youtube_reel are processed fairly
    instead of all rss first, then all youtube_video, etc.
    """
    by_type: dict[str, list[IngestionProgress]] = defaultdict(list)
    for row in rows:
        by_type[row.source_type].append(row)
    
    # Sort type keys for deterministic ordering
    type_keys = sorted(by_type.keys())
    type_lists = [by_type[k] for k in type_keys]
    
    # Interleave: take one from each type in round-robin fashion
    result: List[IngestionProgress] = []
    for batch in zip_longest(*type_lists):
        for item in batch:
            if item is not None:
                result.append(item)
    return result


class IngestionProgressRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, *, day_utc: date, source_type: str, feed_name: str) -> Optional[IngestionProgress]:
        return (
            self.db.query(IngestionProgress)
            .filter(
                IngestionProgress.day_utc == day_utc,
                IngestionProgress.source_type == source_type,
                IngestionProgress.feed_name == feed_name,
            )
            .one_or_none()
        )

    def list_for_day(self, *, day_utc: date) -> List[IngestionProgress]:
        return (
            self.db.query(IngestionProgress)
            .filter(IngestionProgress.day_utc == day_utc)
            .order_by(IngestionProgress.source_type.asc(), IngestionProgress.feed_name.asc())
            .all()
        )

    def list_incomplete(self, *, day_utc: date) -> List[IngestionProgress]:
        """List incomplete rows, interleaved by source type for fair processing."""
        rows = (
            self.db.query(IngestionProgress)
            .filter(
                IngestionProgress.day_utc == day_utc,
                IngestionProgress.status != "complete",
                IngestionProgress.items_ingested < IngestionProgress.target,
            )
            .order_by(IngestionProgress.feed_name.asc())
            .all()
        )
        return _interleave_by_source_type(rows)

    def list_eligible(
        self,
        *,
        day_utc: date,
        now: Optional[datetime] = None,
        source_types: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[IngestionProgress]:
        """List rows eligible to be worked, interleaved by source type.

        Eligibility rules:
        - not complete
        - below target
        - retry_at is null or <= now
        
        Results are interleaved by source_type for fair processing.
        """

        current = now or datetime.utcnow()
        q = self.db.query(IngestionProgress).filter(
            IngestionProgress.day_utc == day_utc,
            IngestionProgress.status.not_in(["complete", "failed"]),
            IngestionProgress.items_ingested < IngestionProgress.target,
            or_(IngestionProgress.retry_at.is_(None), IngestionProgress.retry_at <= current),
        )
        if source_types:
            q = q.filter(IngestionProgress.source_type.in_(list(source_types)))
        
        rows = q.order_by(IngestionProgress.feed_name.asc()).all()
        interleaved = _interleave_by_source_type(rows)
        return interleaved[:limit]

    def ensure_rows(
        self,
        *,
        day_utc: date,
        defaults: Iterable[Tuple[str, str, int]],
    ) -> int:
        """Ensure progress rows exist for the provided defaults.

        Args:
            defaults: iterable of (source_type, feed_name, target)

        Returns:
            Number of rows created.
        """
        existing = {
            (row.source_type, row.feed_name)
            for row in self.db.query(IngestionProgress.source_type, IngestionProgress.feed_name)
            .filter(IngestionProgress.day_utc == day_utc)
            .all()
        }

        created = 0
        now = datetime.utcnow()
        for source_type, feed_name, target in defaults:
            if (source_type, feed_name) in existing:
                continue
            self.db.add(
                IngestionProgress(
                    day_utc=day_utc,
                    source_type=source_type,
                    feed_name=feed_name,
                    target=target,
                    items_ingested=0,
                    status="running",
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1

        if created:
            self.db.commit()
        return created

    def mark_failed(self, row_id: int, error: str) -> None:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.status = "failed"
        row.last_error = error
        row.updated_at = datetime.utcnow()
        self.db.commit()

    def schedule_retry(self, *, row_id: int, error: str, retry_at: datetime) -> None:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.status = "failed"
        row.last_error = error
        row.retry_count = int(row.retry_count or 0) + 1
        row.retry_at = retry_at
        row.updated_at = datetime.utcnow()
        self.db.commit()

    def clear_retry(self, *, row_id: int) -> None:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.retry_at = None
        row.updated_at = datetime.utcnow()
        self.db.commit()

    def update_after_batch(
        self,
        *,
        attempted_count: int = 0,
        row_id: int,
        inserted_count: int,
        new_cursor: Optional[str],
        target: int,
    ) -> IngestionProgress:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.items_attempted = int(row.items_attempted or 0) + int(attempted_count or 0)
        row.items_ingested = int(row.items_ingested or 0) + int(inserted_count or 0)
        if new_cursor:
            row.last_item_cursor = new_cursor
        row.updated_at = datetime.utcnow()
        # A successful batch clears any backoff.
        row.retry_at = None
        if row.items_ingested >= target:
            row.status = "complete"
        else:
            row.status = "running"
        self.db.commit()
        return row
