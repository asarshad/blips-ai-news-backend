"""Source governance models.

`sources` holds per-source configuration (weight, caps, enabled).
`source_daily_stats` tracks per-day insert/suppress counts to enforce caps.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, PrimaryKeyConstraint, String

from app.db.base import Base


class Source(Base):
    __tablename__ = "sources"

    name = Column(String(255), primary_key=True)
    weight = Column(Float, nullable=False, default=1.0)
    daily_cap = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourceDailyStat(Base):
    __tablename__ = "source_daily_stats"

    day = Column(Date, nullable=False)
    source = Column(String(255), nullable=False)

    inserted = Column(Integer, nullable=False, default=0)
    suppressed = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (PrimaryKeyConstraint("day", "source", name="pk_source_daily_stats"),)
