"""Repository for ingestion_budgets.

Uses row locks to guarantee strict caps under concurrency.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.content import ContentType
from app.models.ingestion_budget import IngestionBudget


class IngestionBudgetRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(
        self, *, day: date, content_type: ContentType, for_update: bool = False
    ) -> Optional[IngestionBudget]:
        q = self.db.query(IngestionBudget).filter(
            IngestionBudget.day == day,
            IngestionBudget.content_type == content_type,
        )
        if for_update:
            q = q.with_for_update()
        return q.one_or_none()

    def ensure(self, *, day: date, content_type: ContentType, target: int) -> IngestionBudget:
        row = self.get(day=day, content_type=content_type, for_update=True)
        if row is None:
            row = IngestionBudget(
                day=day,
                content_type=content_type,
                target=int(target or 0),
                reserved=0,
                inserted=0,
                seen=0,
                suppressed=0,
                attempts=0,
            )
            self.db.add(row)
            self.db.commit()
            return row

        # Targets can change across runs; only ever increase to remain additive and deterministic.
        if int(target or 0) > int(row.target or 0):
            row.target = int(target or 0)
        # Always commit to release the FOR UPDATE lock
        self.db.commit()
        return row

    def remaining(self, *, day: date, content_type: ContentType) -> int:
        row = self.get(day=day, content_type=content_type, for_update=False)
        if row is None:
            return 0
        return max(0, int(row.target or 0) - int(row.inserted or 0) - int(row.reserved or 0))

    def reserve(self, *, day: date, content_type: ContentType, want: int) -> int:
        want_n = max(0, int(want or 0))
        if want_n <= 0:
            return 0

        row = self.get(day=day, content_type=content_type, for_update=True)
        if row is None:
            # No lock acquired if row doesn't exist, but commit to release any transactional state
            self.db.commit()
            return 0

        remaining = max(0, int(row.target or 0) - int(row.inserted or 0) - int(row.reserved or 0))
        take = min(want_n, remaining)
        if take <= 0:
            # Release the FOR UPDATE lock without making changes
            self.db.commit()
            return 0

        row.reserved = int(row.reserved or 0) + int(take)
        self.db.commit()
        return int(take)

    def finalize_batch(
        self,
        *,
        day: date,
        content_type: ContentType,
        reserved_taken: int,
        inserted: int,
        seen: int,
        suppressed: int,
        attempts: int,
    ) -> None:
        # reserved_taken slots are released regardless; inserted increments durable count.
        row = self.get(day=day, content_type=content_type, for_update=True)
        if row is None:
            # Release any transactional state even if row doesn't exist
            self.db.commit()
            return

        row.reserved = max(0, int(row.reserved or 0) - int(reserved_taken or 0))
        row.inserted = int(row.inserted or 0) + int(inserted or 0)

        row.seen = int(row.seen or 0) + int(seen or 0)
        row.suppressed = int(row.suppressed or 0) + int(suppressed or 0)
        row.attempts = int(row.attempts or 0) + int(attempts or 0)

        self.db.commit()
