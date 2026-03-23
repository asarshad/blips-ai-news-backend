"""Push notification persistence models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class PushSubscription(Base):
    """Registered push token for a device."""

    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token = Column(Text, nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    active = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    profile = relationship("UserProfile", back_populates="push_subscriptions")

    __table_args__ = (
        UniqueConstraint("device_id", "token", name="uq_push_subscriptions_device_token"),
        Index("ix_push_subscriptions_active_last_seen", "active", "last_seen_at"),
    )


class PushSendLog(Base):
    """Audit log for manual and automatic push sends."""

    __tablename__ = "push_send_logs"

    id = Column(Integer, primary_key=True, index=True)
    content_item_id = Column(
        Integer,
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(String(32), nullable=False, index=True)
    actor = Column(String(255), nullable=True)
    title = Column(String(1024), nullable=False)
    body = Column(Text, nullable=False)
    audience_count = Column(Integer, nullable=False, default=0, server_default="0")
    success_count = Column(Integer, nullable=False, default=0, server_default="0")
    failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    invalid_token_count = Column(Integer, nullable=False, default=0, server_default="0")
    auto_dedup_key = Column(String(255), nullable=True, unique=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    content_item = relationship("ContentItem", back_populates="push_send_logs")

    __table_args__ = (Index("ix_push_send_logs_content_created", "content_item_id", "created_at"),)
