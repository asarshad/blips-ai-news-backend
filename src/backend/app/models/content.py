"""
Unified content models for the curation system.

This module defines the core data models for the signal-driven
content curation system. All content types (ARTICLE, VIDEO, REEL)
are normalized into a single content_items table.
"""

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Date, Boolean, Enum as SQLEnum,
    ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.db.base import Base


class ContentType(enum.Enum):
    """Types of content in the system."""
    ARTICLE = "ARTICLE"
    VIDEO = "VIDEO"
    REEL = "REEL"


class PrefType(enum.Enum):
    """Types of user preferences."""
    TOPIC = "TOPIC"
    ENTITY = "ENTITY"
    SOURCE = "SOURCE"
    FORMAT = "FORMAT"


class EventType(enum.Enum):
    """Types of user interaction events."""
    VIEW_10S = "VIEW_10S"          # Viewed for 10+ seconds
    OPEN_SOURCE = "OPEN_SOURCE"    # Opened external source
    SHARE = "SHARE"                # Shared content
    SAVE = "SAVE"                  # Saved/bookmarked content
    CHAT_START = "CHAT_START"      # Started chat about content
    CHAT_MESSAGE = "CHAT_MESSAGE"  # Sent message in chat


class ContentItem(Base):
    """
    Unified table for all content types.
    
    This is the central table in the curation system, storing
    all articles, videos, and reels with normalized metadata.
    """
    __tablename__ = "content_items"
    
    # Primary key
    id = Column(Integer, primary_key=True, index=True)
    
    # Content type
    type = Column(SQLEnum(ContentType), nullable=False, index=True)
    
    # Source information
    source = Column(String(255), nullable=False, index=True)
    source_url = Column(String(2048), unique=True, nullable=False, index=True)
    canonical_url = Column(String(2048), nullable=True)
    
    # Timing
    published_at = Column(DateTime, nullable=False, index=True)
    
    # Content
    title = Column(String(1024), nullable=False, index=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(2048), nullable=True)
    video_url = Column(String(2048), nullable=True)
    
    # AI-generated content (nullable for reels)
    summary = Column(Text, nullable=True)
    
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
    
    # Deduplication
    dedupe_key = Column(String(128), nullable=True, index=True)
    
    # Video-specific fields
    duration_seconds = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    interactions = relationship("InteractionEvent", back_populates="content_item", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="content_item", cascade="all, delete-orphan")
    
    # Composite indexes for efficient queries
    __table_args__ = (
        # Fast feed queries: recent content of type X with high global score
        Index('ix_content_type_published_score', 'type', 'published_at', 'global_score'),
        # Cluster queries
        Index('ix_content_cluster_type', 'cluster_id', 'type'),
        # Topic-based queries (for personalization)
        Index('ix_content_type_score', 'type', 'global_score'),
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
    preferences = relationship("UserPreference", back_populates="profile", cascade="all, delete-orphan")
    interactions = relationship("InteractionEvent", back_populates="profile", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<UserProfile(device_id={self.device_id})>"


class UserPreference(Base):
    """
    Learned user preferences based on interactions.
    
    Tracks interest weights for topics, entities, sources, and formats.
    """
    __tablename__ = "user_preferences"
    
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(255), ForeignKey("user_profiles.device_id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Preference type and key
    pref_type = Column(SQLEnum(PrefType), nullable=False)
    key = Column(String(255), nullable=False)  # e.g., "AI", "OpenAI", "TechCrunch", "VIDEO"
    
    # Weight (higher = more interest)
    weight = Column(Float, default=0.0, nullable=False)
    
    # Timestamps
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    profile = relationship("UserProfile", back_populates="preferences")
    
    # Unique constraint
    __table_args__ = (
        UniqueConstraint('device_id', 'pref_type', 'key', name='uq_user_pref'),
        Index('ix_pref_device_type', 'device_id', 'pref_type'),
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
    device_id = Column(String(255), ForeignKey("user_profiles.device_id", ondelete="CASCADE"), nullable=False, index=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Event type and value
    event_type = Column(SQLEnum(EventType), nullable=False)
    event_value = Column(String(255), nullable=True)  # Optional metadata (e.g., dwell time)
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    profile = relationship("UserProfile", back_populates="interactions")
    content_item = relationship("ContentItem", back_populates="interactions")
    
    # Indexes for efficient queries
    __table_args__ = (
        Index('ix_interaction_device_created', 'device_id', 'created_at'),
        Index('ix_interaction_content_created', 'content_item_id', 'created_at'),
    )
    
    def __repr__(self):
        return f"<InteractionEvent(device={self.device_id}, content={self.content_item_id}, type={self.event_type.value})>"


# NOTE: ContentCluster table removed - clusters are now implicit via cluster_id on ContentItem.
# Cluster metadata (item_count, etc.) is computed on-the-fly from content_items aggregation.
