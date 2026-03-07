"""
Configuration module.

Centralizes all application configuration and tunable parameters.
"""

from app.config.clustering import ClusteringConfig, clustering_config
from app.config.content import (
    TECH_ENTITIES,
    TECH_TOPICS,
    is_known_entity,
    is_known_topic,
    normalize_entity,
    normalize_topic,
)
from app.config.feeds import (
    FeedSource,
    get_all_feeds,
    get_feed_urls,
    get_feeds_by_category,
)
from app.config.scoring import (
    ENGAGEMENT_WEIGHTS,
    SOURCE_QUALITY_WEIGHTS,
    DiversityConfig,
    RecencyConfig,
    ScoringWeights,
    TrendConfig,
    diversity_config,
    get_engagement_weight,
    get_source_quality,
    recency_config,
    scoring_weights,
    trend_config,
)
from app.config.source_registry import (
    DEFAULT_MAX_DOMAINS,
    DEFAULT_MIN_MENTIONS,
    EXCLUDED_DOMAINS,
    SOURCE_REGISTRY_VERSION,
    SourceRegistryEntry,
    build_tldr_shortlist,
    get_source_registry_entry,
    get_source_registry_stats,
    get_tldr_source_index,
    get_tldr_source_shortlist,
    is_shortlisted_source,
    normalize_domain,
    registered_domain,
)
from app.config.settings import Settings, get_settings, settings
from app.config.source_tiering import (
    BLOCKED_DOMAINS,
    CORE_DOMAINS,
    DISCOVERY_DOMAINS,
    ROTATION_DOMAINS,
    DomainPolicy,
    DomainTier,
    get_domain_policy,
    get_domain_tier,
    is_allowed_domain,
    normalize_domain as normalize_source_domain,
    registered_domain as registered_source_domain,
)
from app.config.youtube import (
    YouTubeChannel,
    get_all_channels,
    get_channel_ids,
    get_channels_by_category,
    get_shorts_channels,
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
    # Source Registry
    "SOURCE_REGISTRY_VERSION",
    "DEFAULT_MIN_MENTIONS",
    "DEFAULT_MAX_DOMAINS",
    "EXCLUDED_DOMAINS",
    "SourceRegistryEntry",
    "normalize_domain",
    "registered_domain",
    "build_tldr_shortlist",
    "get_tldr_source_shortlist",
    "get_tldr_source_index",
    "get_source_registry_entry",
    "is_shortlisted_source",
    "get_source_registry_stats",
    # Source tiering
    "DomainTier",
    "DomainPolicy",
    "CORE_DOMAINS",
    "ROTATION_DOMAINS",
    "DISCOVERY_DOMAINS",
    "BLOCKED_DOMAINS",
    "normalize_source_domain",
    "registered_source_domain",
    "get_domain_tier",
    "get_domain_policy",
    "is_allowed_domain",
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
