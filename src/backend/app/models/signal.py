"""Signal URL tracking model.

``signal_urls`` records every distinct URL discovered by the coverage-guarantee
signal sources (Hacker News, GitHub Trending, YouTube Trending).

These rows are **not** content – they are breadcrumb references that the
PromotionService cross-checks against ``content_items``.  When the signal
orchestrator sees a URL that is absent from ``content_items`` it enqueues it
for ingestion; when it already exists it increments ``content_items.signal_hits``.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

from app.db.base import Base


class SignalSource(enum.Enum):
    """Origin of a signal URL."""

    HN_TOP = "hn_top"  # Hacker News /topstories
    HN_BEST = "hn_best"  # Hacker News /beststories
    GITHUB_TRENDING = "github_trending"
    YT_TRENDING = "yt_trending"  # YouTube Science & Technology most-popular
    DISCOVERY_LEADS = "discovery_leads"  # Substack/Beehiiv discovery feeds


class EnqueueStatus(enum.Enum):
    """Lifecycle state of a discovered signal URL."""

    PENDING = "pending"  # URL seen, not yet processed
    INGESTED = "ingested"  # Enqueued → content_item created
    DUPLICATE = "duplicate"  # Already existed in content_items
    REJECTED = "rejected"  # Failed URL normalization or outside tech scope


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist enum values instead of enum member names."""
    return [member.value for member in enum_cls]


# Standalone PgEnum objects (create_type=False: migration owns type creation)
SignalSourceEnum = PgEnum(
    SignalSource,
    name="signalsource",
    values_callable=_enum_values,
    create_type=False,
)
EnqueueStatusEnum = PgEnum(
    EnqueueStatus,
    name="enqueuedstatus",
    values_callable=_enum_values,
    create_type=False,
)


class SignalURL(Base):
    """Tracks every URL discovered by trend-signal sources.

    The table is append-and-update: each (canonical_url, signal_source)
    pair is unique; repeat sightings bump hit_count and last_seen_at.
    """

    __tablename__ = "signal_urls"

    id = Column(Integer, primary_key=True, index=True)

    # ── URL identity ───────────────────────────────────────────────────────
    raw_url = Column(Text, nullable=False)
    # Normalized (tracking-params stripped, YT canonicalized)
    canonical_url = Column(String(2048), nullable=False, index=True)

    # ── Signal metadata ────────────────────────────────────────────────────
    signal_source = Column(
        SignalSourceEnum,
        nullable=False,
        index=True,
    )
    raw_title = Column(Text, nullable=True)

    # HN score / GitHub star-count / YT view-count (when available)
    signal_score = Column(Integer, nullable=True)

    # ── Lifecycle ──────────────────────────────────────────────────────────────────
    enqueue_status = Column(
        EnqueueStatusEnum,
        nullable=False,
        default=EnqueueStatus.PENDING,
        index=True,
    )

    # FK to content_items.id once the URL has been ingested (nullable)
    content_item_id = Column(
        Integer,
        ForeignKey("content_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Counters ───────────────────────────────────────────────────────────
    # How many times this signal source has reported this URL
    hit_count = Column(Integer, nullable=False, default=1)

    # ── Timestamps ─────────────────────────────────────────────────────────
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    enqueued_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # One row per (canonical_url, signal_source)
        UniqueConstraint("canonical_url", "signal_source", name="uq_signal_url_source"),
        Index("ix_signal_status_source", "enqueue_status", "signal_source"),
    )

    def __repr__(self) -> str:
        return (
            f"<SignalURL(id={self.id}, source={self.signal_source.value}, "
            f"status={self.enqueue_status.value}, url={self.canonical_url[:60]!r})>"
        )
