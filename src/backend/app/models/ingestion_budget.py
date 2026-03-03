"""Ingestion budgets for strict target enforcement.

A budget row represents the global cap for a given (day, content_type).
Workers reserve insert slots before attempting inserts to guarantee:
- deterministic strict caps (no overshoot under concurrency)
- visibility into seen/suppressed/inserted counts

Budgets are created/updated at runtime based on ingestion_progress targets.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Integer, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

from app.db.base import Base
from app.models.content import ContentType

ContentTypeEnum = PgEnum(ContentType, name="contenttype", create_type=False)


class IngestionBudget(Base):
    __tablename__ = "ingestion_budgets"

    day = Column(Date, nullable=False)
    content_type = Column(ContentTypeEnum, nullable=False)

    target = Column(Integer, nullable=False, default=0)

    # Strict-cap accounting
    reserved = Column(Integer, nullable=False, default=0)
    inserted = Column(Integer, nullable=False, default=0)

    # Outcome counters (observability)
    seen = Column(Integer, nullable=False, default=0)
    suppressed = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (PrimaryKeyConstraint("day", "content_type", name="pk_ingestion_budgets"),)
