"""
Unified content models for the curation system.

This module defines the core data models for the signal-driven
content curation system. All content types (ARTICLE, VIDEO, REEL)
are normalized into a single content_items table.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class ContentType(enum.Enum):
    """Types of content in the system."""

    ARTICLE = "ARTICLE"
    VIDEO = "VIDEO"
    REEL = "REEL"


class ContentStatus(enum.Enum):
    """Two-tier pipeline status for content items.

    CANDIDATE  – ingested but not yet promoted; invisible to end-user feeds.
                 AI summarisation is NOT run on candidates (cost control).
    PROMOTED   – passed promotion scoring; shown in feed endpoints and eligible
                 for AI summarisation.

    NOTE: SUPPRESSED content uses the existing ``is_suppressed`` boolean flag.
    ContentStatus only controls the candidate→promoted pipeline.

    Default for newly-ingested review-queue items is CANDIDATE unless
    AUTO_APPROVE_REVIEW_CONTENT is enabled. Manually-added content starts
    as PROMOTED.
    """

    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"


class ContentReadinessStatus(enum.Enum):
    """Client-delivery readiness for a content item."""

    PENDING = "PENDING"
    READY = "READY"


class PrefType(enum.Enum):
    """Types of user preferences."""

    TOPIC = "TOPIC"
    ENTITY = "ENTITY"
    SOURCE = "SOURCE"
    FORMAT = "FORMAT"


class EventType(enum.Enum):
    """Types of user interaction events."""

    VIEW_10S = "VIEW_10S"  # Viewed for 10+ seconds
    OPEN_SOURCE = "OPEN_SOURCE"  # Opened external source
    SHARE = "SHARE"  # Shared content
    SAVE = "SAVE"  # Saved/bookmarked content
    CHAT_START = "CHAT_START"  # Started chat about content
    CHAT_MESSAGE = "CHAT_MESSAGE"  # Sent message in chat
    VIDEO_IMPRESSION = "VIDEO_IMPRESSION"  # Video or reel became visible
    VIDEO_START = "VIDEO_START"  # Playback started
    VIDEO_3S = "VIDEO_3S"  # Reached 3 seconds of playback
    VIDEO_50PCT = "VIDEO_50PCT"  # Reached 50% completion
    VIDEO_95PCT = "VIDEO_95PCT"  # Reached 95% completion
    VIDEO_SKIP_LT_2S = "VIDEO_SKIP_LT_2S"  # Skipped almost immediately
    VIDEO_SAVE = "VIDEO_SAVE"  # Saved/bookmarked video content
    VIDEO_SHARE = "VIDEO_SHARE"  # Shared video content
    LESS_FROM_CREATOR = "LESS_FROM_CREATOR"  # Explicit creator downvote
    CAUGHT_UP = "CAUGHT_UP"  # User hit the end of healthy inventory


# Define PostgreSQL enums with create_type=False to avoid recreation errors
# These types are created by Alembic migrations, not by SQLAlchemy
ContentTypeEnum = PgEnum(ContentType, name="contenttype", create_type=False)
ContentStatusEnum = PgEnum(ContentStatus, name="contentstatus", create_type=False)
PrefTypeEnum = PgEnum(PrefType, name="preftype", create_type=False)
EventTypeEnum = PgEnum(EventType, name="eventtype", create_type=False)


class ContentItem(Base):
    """
    Unified table for all content types.

    This is the central table in the curation system, storing
    all articles, videos, and reels with normalized metadata.
    """

    __tablename__ = "content_items"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Content type - use pre-defined enum to avoid create_type issues
    type = Column(ContentTypeEnum, nullable=False, index=True)

    # Source information
    source = Column(String(255), nullable=False, index=True)
    source_url = Column(String(2048), unique=True, nullable=False, index=True)
    canonical_url = Column(String(2048), nullable=True)
    channel_id = Column(String(64), nullable=True, index=True)

    # Canonical identity (hard-dedupe)
    # - ARTICLE: sha256(normalized canonical URL)
    # - VIDEO/REEL: YouTube video_id when available
    canonical_key = Column(String(64), nullable=True, index=True)

    # Ingestion day for strict target accounting (timezone-aware at write time)
    ingestion_day = Column(Date, nullable=True, index=True)

    # Suppression flag (hidden from default feeds)
    is_suppressed = Column(Boolean, default=False, nullable=False, index=True)

    # ── Two-tier pipeline status ───────────────────────────────────────────
    # CANDIDATE = ingested but not yet promoted (no AI, not in feed)
    # PROMOTED  = passed quality gate, eligible for AI + feed
    # Default is PROMOTED for backward compatibility with pre-existing items
    # and manually-added content. Review-queue ingestion paths explicitly
    # decide between CANDIDATE and PROMOTED at runtime.
    curation_status = Column(
        ContentStatusEnum,
        nullable=False,
        default=ContentStatus.PROMOTED,
        server_default="PROMOTED",
        index=True,
    )
    readiness_status = Column(
        String(32),
        nullable=False,
        default=ContentReadinessStatus.PENDING.value,
        server_default=ContentReadinessStatus.PENDING.value,
        index=True,
    )
    readiness_reason = Column(String(64), nullable=True)
    ready_at = Column(DateTime, nullable=True, index=True)
    readiness_updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    # How this item was discovered: 'rss', 'yt_ingestion', 'manual',
    # 'signal_hn', 'signal_github', 'signal_yt_trending', 'signal_discovery'
    discovered_via = Column(String(64), nullable=True, index=True)

    # Number of distinct signal sources (HN/GitHub/YT trending) that
    # referenced this URL – used as a hotness booster in promotion scoring.
    signal_hits = Column(Integer, default=0, nullable=False)

    # Promotion score computed by PromotionService (refreshed each run).
    promotion_score = Column(Float, nullable=True, index=True)
    promotion_reason = Column(String(255), nullable=True)

    # Dedicated video/reel discovery metadata.
    acquisition_lane = Column(String(32), nullable=True, index=True)
    source_status = Column(String(32), nullable=True, index=True)
    view_count_snapshot = Column(BigInteger, nullable=True)
    engagement_snapshot = Column(JSONB, nullable=True)
    views_per_hour = Column(Float, nullable=True, index=True)
    format_fit_score = Column(Float, nullable=True)
    tech_relevance = Column(String(16), nullable=True, index=True)
    tech_relevance_confidence = Column(Float, nullable=True)
    tech_relevance_reason = Column(String(255), nullable=True)
    is_major_tech_news = Column(Boolean, nullable=True, index=True)
    major_tech_news_confidence = Column(Float, nullable=True)
    major_tech_news_reason = Column(String(255), nullable=True)
    is_mixed_roundup = Column(Boolean, nullable=True, index=True)
    # Audience lane: "GENERAL_PUBLIC" (accessible to non-technical readers) or
    # "TECHIES" (primarily for developers / engineers).  NULL = not yet classified.
    audience_lane = Column(String(32), nullable=True, index=True)
    audience_lane_confidence = Column(Float, nullable=True)
    audience_lane_reason = Column(String(255), nullable=True)

    # Candidate provenance fields (set by signal ingestion when created as CANDIDATE).
    candidate_first_seen_at = Column(DateTime, nullable=True, index=True)
    candidate_signal_source = Column(String(64), nullable=True, index=True)
    candidate_raw_title = Column(Text, nullable=True)
    # ─────────────────────────────────────────────────────────────────────

    # ── Editorial control fields ──────────────────────────────────────────
    editorial_boost = Column(Integer, default=0, nullable=False, index=True)
    manual_added = Column(Boolean, default=False, nullable=False)
    added_by = Column(Text, nullable=True)
    added_at = Column(DateTime, nullable=True)
    last_modified_by = Column(Text, nullable=True)
    last_modified_at = Column(DateTime, nullable=True)
    # ─────────────────────────────────────────────────────────────────────

    # Timing
    published_at = Column(DateTime, nullable=False, index=True)

    # Content
    title = Column(String(1024), nullable=False, index=True)
    description = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    image_url = Column(String(2048), nullable=True)
    article_image_status = Column(String(32), nullable=True, index=True)
    article_image_checked_at = Column(DateTime, nullable=True, index=True)
    video_url = Column(String(2048), nullable=True)

    # AI-generated content (nullable for reels)
    summary = Column(Text, nullable=True)

    # AI-generated conversation starters
    # Format: {"starters": ["Q1", "Q2", "Q3"], "fallback": ["Q1", "Q2"]}
    conversation_starters = Column(JSONB, nullable=True)
    # Precomputed answers for exact starter prompts
    # Format: {"Prompt text": "Answer text", ...}
    starter_answers = Column(JSONB, nullable=True)

    # AI processing status
    ai_processed = Column(Boolean, default=False, nullable=False, index=True)

    # Extracted metadata (JSONB arrays)
    topics = Column(JSONB, nullable=False, default=list)  # ["AI", "Machine Learning"]
    entities = Column(JSONB, nullable=False, default=list)  # [{"name": "OpenAI", "type": "ORG"}]

    # Scoring fields
    quality_score = Column(Float, default=0.5, index=True)
    trend_score = Column(Float, default=0.0, index=True)
    recency_score = Column(Float, default=1.0, index=True)
    diversity_boost = Column(Float, default=0.0)
    global_score = Column(Float, default=0.0, index=True)

    # Clustering
    cluster_id = Column(String(64), nullable=True, index=True)
    is_cluster_canonical = Column(Integer, default=0)  # 1 if canonical for its cluster+type

    # Near-duplicate signature (articles)
    simhash = Column(BigInteger, nullable=True, index=True)

    # Deduplication
    dedupe_key = Column(String(128), nullable=True, index=True)

    # Language of the content (ISO 639-1 code, e.g. "en", "es").
    # NULL means pre-existing rows ingested before language detection was added;
    # they should be treated as "en" by feed queries (IS NULL OR = 'en').
    language = Column(String(10), nullable=True, index=True)

    # Video-specific fields
    duration_seconds = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    interactions = relationship(
        "InteractionEvent", back_populates="content_item", cascade="all, delete-orphan"
    )
    conversations = relationship(
        "Conversation", back_populates="content_item", cascade="all, delete-orphan"
    )
    push_send_logs = relationship(
        "PushSendLog",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )
    content_events = relationship(
        "ContentEventOutbox",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )

    # Composite indexes for efficient queries
    __table_args__ = (
        # Fast feed queries: recent content of type X with high global score
        Index("ix_content_type_published_score", "type", "published_at", "global_score"),
        # Cluster queries
        Index("ix_content_cluster_type", "cluster_id", "type"),
        # Topic-based queries (for personalization)
        Index("ix_content_type_score", "type", "global_score"),
        # Promotion pipeline: find CANDIDATEs for a given type quickly
        Index("ix_content_curation_type_score", "curation_status", "type", "promotion_score"),
        Index("ix_content_readiness_type_published", "readiness_status", "type", "published_at"),
    )

    def __repr__(self):
        return f"<ContentItem(id={self.id}, type={self.type.value}, title={self.title[:50]}...)>"


class UserProfile(Base):
    """
    User profile identified by device_id.

    Minimal profile for tracking preferences without PII.
    """

    __tablename__ = "user_profiles"

    device_id = Column(String(255), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    preferences = relationship(
        "UserPreference", back_populates="profile", cascade="all, delete-orphan"
    )
    interactions = relationship(
        "InteractionEvent", back_populates="profile", cascade="all, delete-orphan"
    )
    category_selections = relationship(
        "UserCategorySelection",
        back_populates="profile",
        cascade="all, delete-orphan",
        uselist=False,
    )
    push_subscriptions = relationship(
        "PushSubscription",
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    device_session = relationship(
        "DeviceSession",
        back_populates="profile",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self):
        return f"<UserProfile(device_id={self.device_id})>"


class UserPreference(Base):
    """
    Learned user preferences based on interactions.

    Tracks interest weights for topics, entities, sources, and formats.
    """

    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Preference type and key - use pre-defined enum to avoid create_type issues
    pref_type = Column(PrefTypeEnum, nullable=False)
    key = Column(String(255), nullable=False)  # e.g., "AI", "OpenAI", "TechCrunch", "VIDEO"

    # Weight (higher = more interest)
    weight = Column(Float, default=0.0, nullable=False)

    # Timestamps
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    profile = relationship("UserProfile", back_populates="preferences")

    # Unique constraint
    __table_args__ = (
        UniqueConstraint("device_id", "pref_type", "key", name="uq_user_pref"),
        Index("ix_pref_device_type", "device_id", "pref_type"),
    )

    def __repr__(self):
        return f"<UserPreference(device_id={self.device_id}, {self.pref_type.value}:{self.key}={self.weight})>"


class InteractionEvent(Base):
    """
    Append-only table for raw interaction signals.

    Captures all user interactions for preference learning
    and analytics.
    """

    __tablename__ = "interaction_events"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_item_id = Column(
        Integer, ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Event type and value - use pre-defined enum to avoid create_type issues
    event_type = Column(EventTypeEnum, nullable=False)
    event_value = Column(String(255), nullable=True)  # Optional metadata (e.g., dwell time)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    profile = relationship("UserProfile", back_populates="interactions")
    content_item = relationship("ContentItem", back_populates="interactions")

    # Indexes for efficient queries
    __table_args__ = (
        Index("ix_interaction_device_created", "device_id", "created_at"),
        Index("ix_interaction_content_created", "content_item_id", "created_at"),
    )

    def __repr__(self):
        return f"<InteractionEvent(device={self.device_id}, content={self.content_item_id}, type={self.event_type.value})>"


class UserCategorySelection(Base):
    """
    User-declared category interests set during onboarding.

    Distinct from UserPreference (which stores *learned* weights from
    interaction history).  This table stores explicit opt-in categories
    chosen by the user (e.g. ["AI", "Security", "Open Source"]).

    These drive an additive interest_boost in the personalized feed score.
    The boost decays as the user builds up engagement-derived preferences,
    so that actual behaviour eventually overrides declared intent.
    """

    __tablename__ = "user_category_selections"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Ordered list of category strings the user selected, e.g. ["AI", "Security"]
    selected_categories = Column(JSONB, nullable=False, default=list)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship back to profile
    profile = relationship("UserProfile", back_populates="category_selections")

    def __repr__(self):
        return (
            f"<UserCategorySelection(device_id={self.device_id}, "
            f"categories={self.selected_categories})>"
        )


class ContentReport(Base):
    """
    User-submitted moderation reports for content items and AI chat responses.

    Created by POST /session/reports when a user taps Report or Block Source.
    Reviewed by the operator within 24 hours per the Terms of Service.
    """

    __tablename__ = "content_reports"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(255),
        ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_item_id = Column(
        Integer,
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Surface where the report originated: 'articles', 'videos', 'reels', 'chat'
    surface = Column(String(32), nullable=False)
    # Reason key: 'hateful', 'violence', 'explicit', 'spam', 'misinformation',
    #             'other', 'blocked_source'
    reason = Column(String(64), nullable=False)
    # For chat reports — identifies the specific AI message
    message_id = Column(String(255), nullable=True)
    # Moderation workflow
    reviewed = Column(Boolean, default=False, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_content_reports_device_created", "device_id", "created_at"),
        Index("ix_content_reports_reviewed", "reviewed", "created_at"),
    )


# NOTE: ContentCluster table removed - clusters are now implicit via cluster_id on ContentItem.
# Cluster metadata (item_count, etc.) is computed on-the-fly from content_items aggregation.
