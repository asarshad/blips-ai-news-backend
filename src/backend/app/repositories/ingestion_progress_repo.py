"""Repository for ingestion progress rows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.ingestion_progress import IngestionProgress


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
        return (
            self.db.query(IngestionProgress)
            .filter(
                IngestionProgress.day_utc == day_utc,
                IngestionProgress.status != "complete",
                IngestionProgress.items_ingested < IngestionProgress.target,
            )
            .order_by(IngestionProgress.source_type.asc(), IngestionProgress.feed_name.asc())
            .all()
        )

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

    def update_after_batch(
        self,
        *,
        row_id: int,
        inserted_count: int,
        new_cursor: Optional[str],
        target: int,
    ) -> IngestionProgress:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.items_ingested = int(row.items_ingested or 0) + int(inserted_count or 0)
        if new_cursor:
            row.last_item_cursor = new_cursor
        row.updated_at = datetime.utcnow()
        if row.items_ingested >= target:
            row.status = "complete"
        else:
            row.status = "running"
        self.db.commit()
        return row
