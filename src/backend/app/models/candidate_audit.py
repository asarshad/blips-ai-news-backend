"""Candidate ingestion audit model.

Tracks provenance and lifecycle events for candidate URLs flowing through the
signal ingestion pipeline.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class CandidateAuditEvent(Base):
    __tablename__ = "candidate_audit_events"

    id = Column(Integer, primary_key=True, index=True)

    content_item_id = Column(
        Integer,
        ForeignKey("content_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    canonical_url = Column(String(2048), nullable=False, index=True)
    signal_source = Column(String(64), nullable=False, index=True)
    discovered_via = Column(String(64), nullable=True, index=True)
    event_type = Column(
        String(32), nullable=False, index=True
    )  # created|duplicate|rejected|skipped

    reason = Column(Text, nullable=True)
    payload = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
