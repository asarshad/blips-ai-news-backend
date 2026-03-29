"""Anonymous bearer-session persistence model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceSession(Base):
    """Single active bearer session for an anonymous app install."""

    __tablename__ = "device_sessions"

    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        primary_key=True,
    )
    platform = Column(String(32), nullable=False, index=True)
    app_version = Column(String(64), nullable=True)
    refresh_token_hash = Column(String(128), nullable=False, index=True)
    issued_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)

    profile = relationship("UserProfile", back_populates="device_session")

    __table_args__ = (Index("ix_device_sessions_active_expiry", "revoked_at", "expires_at"),)
