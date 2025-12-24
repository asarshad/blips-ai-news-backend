"""
Configuration module.

Centralizes all application configuration and tunable parameters.
"""

from app.config.settings import Settings, get_settings, settings
from app.config.scoring import (
    ScoringWeights,
    RecencyConfig,
    DiversityConfig,
    TrendConfig,
    SOURCE_QUALITY_WEIGHTS,
    ENGAGEMENT_WEIGHTS,
    get_source_quality,
    get_engagement_weight,
    scoring_weights,
    recency_config,
    diversity_config,
    trend_config,
)
from app.config.clustering import ClusteringConfig, clustering_config
from app.config.content import (
    TECH_TOPICS,
    TECH_ENTITIES,
    normalize_topic,
    normalize_entity,
    is_known_topic,
    is_known_entity,
)
from app.config.feeds import (
    FeedSource,
    get_all_feeds,
    get_feed_urls,
    get_feeds_by_category,
)
from app.config.youtube import (
    YouTubeChannel,
    get_all_channels,
    get_shorts_channels,
    get_channel_ids,
    get_channels_by_category,
)

__all__ = [
    # Settings
    "Settings",
    "get_settings",
    "settings",
    
    # Scoring
    "ScoringWeights",
    "RecencyConfig",
    "DiversityConfig",
    "TrendConfig",
    "SOURCE_QUALITY_WEIGHTS",
    "ENGAGEMENT_WEIGHTS",
    "get_source_quality",
    "get_engagement_weight",
    "scoring_weights",
    "recency_config",
    "diversity_config",
    "trend_config",
    
    # Clustering
    "ClusteringConfig",
    "clustering_config",
    
    # Content
    "TECH_TOPICS",
    "TECH_ENTITIES",
    "normalize_topic",
    "normalize_entity",
    "is_known_topic",
    "is_known_entity",
    
    # Feeds
    "FeedSource",
    "get_all_feeds",
    "get_feed_urls",
    "get_feeds_by_category",
    
    # YouTube
    "YouTubeChannel",
    "get_all_channels",
    "get_shorts_channels",
    "get_channel_ids",
    "get_channels_by_category",
]
