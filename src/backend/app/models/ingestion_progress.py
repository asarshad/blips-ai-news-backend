"""Ingestion progress tracking.

This table stores per-feed (or per-channel) progress checkpoints for durable,
restart-resilient ingestion. Rows are keyed by (day_utc, source_type, feed_name).

Design goals:
- Safe to resume after restarts
- Supports per-feed targets
- Works with Redis leases to avoid duplicate work across processes
"""

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, UniqueConstraint, Index

from app.db.base import Base


class IngestionProgress(Base):
    __tablename__ = "ingestion_progress"

    id = Column(Integer, primary_key=True)

    # Scope
    day_utc = Column(Date, nullable=False, index=True)
    source_type = Column(String(32), nullable=False, index=True)  # rss | youtube_video | youtube_reel
    feed_name = Column(String(255), nullable=False, index=True)

    # Targets / counters
    target = Column(Integer, nullable=False)
    items_ingested = Column(Integer, nullable=False, default=0)

    # Cursor checkpoint (feed-dependent; newest-first feeds store last seen item key)
    last_item_cursor = Column(String(2048), nullable=True)

    # Status
    status = Column(String(32), nullable=False, default="running")  # running | complete | failed | disabled
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "day_utc",
            "source_type",
            "feed_name",
            name="uq_ingestion_progress_day_source_feed",
        ),
        Index("ix_ingestion_progress_scope", "day_utc", "source_type", "feed_name"),
    )
