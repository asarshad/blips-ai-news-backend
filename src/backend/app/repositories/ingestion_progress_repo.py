"""Repository for ingestion progress rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from itertools import zip_longest
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import and_, or_
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

    def get(
        self, *, day_utc: date, source_type: str, feed_name: str
    ) -> Optional[IngestionProgress]:
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
        - below target
        - either:
          - status is active (not complete/failed) and retry_at is null or <= now
          - status is failed but explicitly scheduled for retry (retry_at <= now)

        Results are interleaved by source_type for fair processing.
        """

        current = now or datetime.utcnow()
        q = self.db.query(IngestionProgress).filter(
            IngestionProgress.day_utc == day_utc,
            IngestionProgress.items_ingested < IngestionProgress.target,
            or_(
                and_(
                    IngestionProgress.status.not_in(["complete", "failed"]),
                    or_(
                        IngestionProgress.retry_at.is_(None), IngestionProgress.retry_at <= current
                    ),
                ),
                and_(
                    IngestionProgress.status == "failed",
                    IngestionProgress.retry_at.isnot(None),
                    IngestionProgress.retry_at <= current,
                ),
            ),
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

    def reopen_continuous_rows(
        self,
        *,
        day_utc: date,
        defaults: Iterable[Tuple[str, str, int]],
    ) -> int:
        """Reopen completed continuous-ingestion rows for the next cycle.

        For each row that has met its target or has exhausted its
        per-cycle attempt budget, bump the target additively and clear
        exhaustion state so the checkpoint loop picks it up again.

        Uses ``skip_locked=True`` to avoid clobbering a row that another
        process (top-up or scheduler) is actively working on.
        """
        reopened = 0
        now = datetime.utcnow()
        for source_type, feed_name, per_cycle_target in defaults:
            row = (
                self.db.query(IngestionProgress)
                .filter(
                    IngestionProgress.day_utc == day_utc,
                    IngestionProgress.source_type == source_type,
                    IngestionProgress.feed_name == feed_name,
                )
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if row is None:
                continue
            reached_target = int(row.items_ingested or 0) >= int(row.target or 0)
            exhausted = row.status == "failed" and "Exhausted attempts:" in str(
                row.last_error or ""
            )
            # Only reopen rows that are finished for this cycle or failed due
            # to exhaustion. Unknown-channel/source failures should remain failed.
            if not reached_target and row.status not in ("complete",) and not exhausted:
                continue
            # Bump target so remaining = per_cycle_target
            row.target = int(row.items_ingested or 0) + per_cycle_target
            row.status = "running"
            row.items_attempted = 0
            row.retry_count = 0
            row.retry_at = None
            row.last_error = None
            row.updated_at = now
            reopened += 1
        if reopened:
            self.db.commit()
        return reopened

    def reopen_youtube_rows(
        self,
        *,
        day_utc: date,
        defaults: Iterable[Tuple[str, str, int]],
    ) -> int:
        """Backward-compatible YouTube-only reopen helper used by older tests/callers."""
        youtube_defaults = [
            (source_type, feed_name, target)
            for source_type, feed_name, target in defaults
            if source_type != "rss"
        ]
        return self.reopen_continuous_rows(day_utc=day_utc, defaults=youtube_defaults)

    def prime_youtube_rows(
        self,
        *,
        day_utc: date,
        rows: Iterable[Tuple[str, str, int]],
    ) -> List[int]:
        """Reset selected YouTube rows for a bounded bootstrap/backfill pass.

        The row cursor is cleared so the next batch starts from the newest entries.
        Existing ``items_ingested`` is preserved; the target is only raised when the
        bootstrap target is larger than the current row target.
        """
        primed_ids: List[int] = []
        now = datetime.utcnow()
        for source_type, feed_name, bootstrap_target in rows:
            if source_type == "rss":
                continue
            row = (
                self.db.query(IngestionProgress)
                .filter(
                    IngestionProgress.day_utc == day_utc,
                    IngestionProgress.source_type == source_type,
                    IngestionProgress.feed_name == feed_name,
                )
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if row is None:
                continue
            row.target = max(int(row.target or 0), int(bootstrap_target or 0))
            row.status = "running"
            row.items_attempted = 0
            row.retry_count = 0
            row.retry_at = None
            row.last_error = None
            row.last_item_cursor = None
            row.updated_at = now
            primed_ids.append(int(row.id))
        if primed_ids:
            self.db.commit()
        return primed_ids

    def mark_failed(self, row_id: int, error: str) -> None:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        row.status = "failed"
        row.last_error = error
        row.updated_at = datetime.utcnow()
        self.db.commit()

    def schedule_retry(self, *, row_id: int, error: str, retry_at: datetime) -> None:
        row = self.db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
        # Retryable failures should remain eligible after backoff expires.
        row.status = "running"
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

    def add_target(
        self,
        *,
        day_utc: date,
        source_type: str,
        feed_name: str,
        amount: int,
    ) -> Optional[IngestionProgress]:
        row = (
            self.db.query(IngestionProgress)
            .filter(
                IngestionProgress.day_utc == day_utc,
                IngestionProgress.source_type == source_type,
                IngestionProgress.feed_name == feed_name,
            )
            .one_or_none()
        )
        if row is None:
            return None

        increment = max(0, int(amount or 0))
        if increment <= 0:
            return row

        row.target = int(row.target or 0) + increment
        if row.status == "complete" and int(row.items_ingested or 0) < int(row.target or 0):
            row.status = "running"
        row.updated_at = datetime.utcnow()
        self.db.commit()
        return row

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
