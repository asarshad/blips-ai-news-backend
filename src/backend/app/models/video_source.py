"""Video source governance and discovery metrics models."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String

from app.db.base import Base


class VideoSourceProfile(Base):
    """Governance record for a YouTube channel used in videos or reels."""

    __tablename__ = "video_source_profiles"

    channel_id = Column(String(64), primary_key=True)
    channel_name = Column(String(255), nullable=False, index=True)
    role = Column(String(64), nullable=False, index=True)
    content_format = Column(String(32), nullable=False)
    quality_tier = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="discovery", index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    curated_seed = Column(Boolean, nullable=False, default=False)

    daily_video_cap = Column(Integer, nullable=False, default=2)
    daily_reel_cap = Column(Integer, nullable=False, default=4)

    allow_curated = Column(Boolean, nullable=False, default=True)
    allow_search = Column(Boolean, nullable=False, default=True)
    allow_trending = Column(Boolean, nullable=False, default=True)

    score_7d = Column(Float, nullable=False, default=0.0)
    promotion_rate_7d = Column(Float, nullable=False, default=0.0)
    suppression_rate_7d = Column(Float, nullable=False, default=0.0)
    clickbait_rate_7d = Column(Float, nullable=False, default=0.0)
    freshness_yield_7d = Column(Float, nullable=False, default=0.0)
    post_start_consumption_7d = Column(Float, nullable=False, default=0.0)
    promoted_share_7d = Column(Float, nullable=False, default=0.0)
    early_skip_rate_7d = Column(Float, nullable=False, default=0.0)
    completion_rate_7d = Column(Float, nullable=False, default=0.0)
    save_share_rate_7d = Column(Float, nullable=False, default=0.0)

    last_seen_at = Column(DateTime, nullable=True, index=True)
    last_promoted_at = Column(DateTime, nullable=True, index=True)
    status_changed_at = Column(DateTime, nullable=True)
    probation_until = Column(DateTime, nullable=True)
    low_promotion_since = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_video_source_status_enabled", "status", "enabled"),
        Index("ix_video_source_role_status", "role", "status"),
    )


class VideoDiscoveryRun(Base):
    """Aggregated counts for a single discovery lane execution."""

    __tablename__ = "video_discovery_runs"

    id = Column(Integer, primary_key=True, index=True)
    lane = Column(String(32), nullable=False, index=True)
    surface = Column(String(16), nullable=False, index=True)
    query_label = Column(String(128), nullable=True, index=True)
    region = Column(String(8), nullable=True, index=True)

    candidate_count = Column(Integer, nullable=False, default=0)
    promoted_count = Column(Integer, nullable=False, default=0)
    distinct_promoted_channels = Column(Integer, nullable=False, default=0)
    median_promoted_age_hours = Column(Float, nullable=True)

    duplicate_rejections = Column(Integer, nullable=False, default=0)
    clickbait_rejections = Column(Integer, nullable=False, default=0)
    filtered_non_english = Column(Integer, nullable=False, default=0)
    filtered_live = Column(Integer, nullable=False, default=0)
    filtered_off_topic = Column(Integer, nullable=False, default=0)
    filtered_format = Column(Integer, nullable=False, default=0)

    run_started_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_video_discovery_lane_surface_started", "lane", "surface", "run_started_at"),
    )
