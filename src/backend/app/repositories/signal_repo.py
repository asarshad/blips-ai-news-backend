"""Repository for SignalURL model.

Provides upsert-style access so that running the same signal source
twice in a row only bumps hit_count rather than inserting duplicate rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.signal import EnqueueStatus, SignalSource, SignalURL


class SignalURLRepository:
    """Data access layer for signal_urls."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Upsert ────────────────────────────────────────────────────────────

    def upsert(
        self,
        *,
        raw_url: str,
        canonical_url: str,
        signal_source: SignalSource,
        raw_title: Optional[str] = None,
        signal_score: Optional[int] = None,
    ) -> SignalURL:
        """Atomically insert a new signal URL or bump hit_count if it exists.

        Uses PostgreSQL ``INSERT … ON CONFLICT DO UPDATE`` so two concurrent
        scheduler runs cannot both INSERT the same row (eliminates the TOCTOU
        race in the old SELECT-then-INSERT pattern).

        Returns the persisted SignalURL.  Does NOT flush/commit – caller owns
        the transaction so that batches can be committed together.
        """
        now = datetime.utcnow()

        stmt = (
            pg_insert(SignalURL)
            .values(
                raw_url=raw_url,
                canonical_url=canonical_url,
                signal_source=signal_source,
                raw_title=raw_title,
                signal_score=signal_score,
                enqueue_status=EnqueueStatus.PENDING,
                hit_count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_signal_url_source",
                set_={
                    "hit_count": SignalURL.__table__.c.hit_count + 1,
                    "last_seen_at": now,
                    "signal_score": signal_score,
                },
            )
        )
        self.db.execute(stmt)
        self.db.flush()

        # Re-fetch to get the ORM-tracked row with its current hit_count state.
        return (
            self.db.query(SignalURL)
            .filter(
                SignalURL.canonical_url == canonical_url,
                SignalURL.signal_source == signal_source,
            )
            .with_for_update()
            .one()
        )

    # ── Status transitions ────────────────────────────────────────────────

    def mark_ingested(self, signal_url: SignalURL, content_item_id: int) -> None:
        signal_url.enqueue_status = EnqueueStatus.INGESTED
        signal_url.content_item_id = content_item_id
        signal_url.enqueued_at = datetime.utcnow()

    def mark_duplicate(self, signal_url: SignalURL, content_item_id: int) -> None:
        signal_url.enqueue_status = EnqueueStatus.DUPLICATE
        signal_url.content_item_id = content_item_id

    def mark_rejected(self, signal_url: SignalURL) -> None:
        signal_url.enqueue_status = EnqueueStatus.REJECTED

    # ── Queries ───────────────────────────────────────────────────────────

    def get_pending(self, limit: int = 200) -> List[SignalURL]:
        """Return PENDING rows ordered oldest-first."""
        return (
            self.db.query(SignalURL)
            .filter(SignalURL.enqueue_status == EnqueueStatus.PENDING)
            .order_by(SignalURL.first_seen_at)
            .limit(limit)
            .all()
        )

    def count_by_status(self) -> Dict[str, int]:
        """Return {status_value: count} for all rows in the table."""
        rows = (
            self.db.query(SignalURL.enqueue_status, func.count(SignalURL.id))
            .group_by(SignalURL.enqueue_status)
            .all()
        )
        return {row[0].value: row[1] for row in rows}

    def count_seen_last_hours(self, hours: int = 24) -> int:
        """Count signal URLs first seen in the last N hours."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return int(
            self.db.query(func.count(SignalURL.id))
            .filter(SignalURL.first_seen_at >= cutoff)
            .scalar()
            or 0
        )

    def count_added_last_hours(self, hours: int = 24) -> int:
        """Count signal URLs that resulted in new content items in the last N hours."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return int(
            self.db.query(func.count(SignalURL.id))
            .filter(
                SignalURL.enqueue_status == EnqueueStatus.INGESTED,
                SignalURL.enqueued_at >= cutoff,
            )
            .scalar()
            or 0
        )
