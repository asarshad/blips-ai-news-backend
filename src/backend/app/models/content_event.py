"""Durable outbox records for content lifecycle events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class ContentEventOutbox(Base):
    """Outbox row for content lifecycle events."""

    __tablename__ = "content_event_outbox"

    id = Column(Integer, primary_key=True, index=True)
    content_item_id = Column(
        Integer,
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSONB, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    locked_at = Column(DateTime, nullable=True, index=True)
    processed_at = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    content_item = relationship("ContentItem", back_populates="content_events")

    __table_args__ = (
        Index("ix_content_event_outbox_status_available", "status", "available_at"),
        Index(
            "ix_content_event_outbox_content_created",
            "content_item_id",
            "created_at",
        ),
    )
