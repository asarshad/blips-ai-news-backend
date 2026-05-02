"""Durable fetch-response state for external ingestion sources."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.db.base import Base


class SourceFetchState(Base):
    __tablename__ = "source_fetch_states"

    id = Column(Integer, primary_key=True)

    source_type = Column(String(32), nullable=False, index=True)
    feed_name = Column(String(255), nullable=False, index=True)
    source_url = Column(Text, nullable=False)

    health_status = Column(String(32), nullable=False, default="healthy", index=True)
    last_action = Column(String(64), nullable=False, default="success", index=True)
    last_http_status = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)

    retry_after_seconds = Column(Integer, nullable=True)
    cooldown_until = Column(DateTime, nullable=True, index=True)
    canonical_url = Column(Text, nullable=True)
    redirect_count = Column(Integer, nullable=False, default=0)

    success_count = Column(Integer, nullable=False, default=0)
    failure_count = Column(Integer, nullable=False, default=0)
    consecutive_failure_count = Column(Integer, nullable=False, default=0)

    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "feed_name",
            name="uq_source_fetch_states_source_feed",
        ),
        Index("ix_source_fetch_states_status_cooldown", "health_status", "cooldown_until"),
    )
