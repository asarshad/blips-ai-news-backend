"""Repositories for source governance."""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.source import Source, SourceDailyStat


class SourceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, *, name: str, for_update: bool = False) -> Optional[Source]:
        q = self.db.query(Source).filter(Source.name == name)
        if for_update:
            q = q.with_for_update()
        return q.one_or_none()

    def ensure(self, *, name: str) -> Source:
        row = self.get(name=name, for_update=True)
        if row is None:
            row = Source(name=name, weight=1.0, enabled=True)
            self.db.add(row)
            self.db.commit()
        return row


class SourceDailyStatsRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, *, day: date, source: str, for_update: bool = False) -> Optional[SourceDailyStat]:
        q = self.db.query(SourceDailyStat).filter(
            SourceDailyStat.day == day, SourceDailyStat.source == source
        )
        if for_update:
            q = q.with_for_update()
        return q.one_or_none()

    def ensure(self, *, day: date, source: str) -> SourceDailyStat:
        row = self.get(day=day, source=source, for_update=True)
        if row is None:
            row = SourceDailyStat(day=day, source=source, inserted=0, suppressed=0)
            self.db.add(row)
            self.db.commit()
        return row

    def can_insert(self, *, day: date, source: str, cap: Optional[int]) -> bool:
        if cap is None:
            return True
        stat = self.ensure(day=day, source=source)
        return int(stat.inserted or 0) < int(cap)

    def record_inserted(self, *, day: date, source: str, count: int) -> None:
        stat = self.get(day=day, source=source, for_update=True)
        if stat is None:
            stat = SourceDailyStat(day=day, source=source, inserted=0, suppressed=0)
            self.db.add(stat)
        stat.inserted = int(stat.inserted or 0) + int(count or 0)
        self.db.commit()

    def record_suppressed(self, *, day: date, source: str, count: int) -> None:
        stat = self.get(day=day, source=source, for_update=True)
        if stat is None:
            stat = SourceDailyStat(day=day, source=source, inserted=0, suppressed=0)
            self.db.add(stat)
        stat.suppressed = int(stat.suppressed or 0) + int(count or 0)
        self.db.commit()
