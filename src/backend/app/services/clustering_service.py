"""
Clustering service for grouping related content.

DEPRECATED: This module has been refactored into app.clustering

The functionality has been split into:
- app.clustering.similarity - Similarity computation functions
- app.clustering.dedupe - Deduplication utilities
- app.clustering.service - ClusteringService class

This file remains for backward compatibility. Import from app.clustering instead.
"""

# Re-export from new location for backward compatibility
from app.clustering import (
    ClusteringService,
    compute_dedupe_key,
    compute_similarity,
)

# Re-export config for backward compatibility
from app.config.clustering import clustering_config

# Expose config values for backward compatibility
CLUSTER_WINDOW_HOURS = clustering_config.window_hours
MIN_ENTITY_OVERLAP = clustering_config.min_entity_overlap
MIN_TITLE_SIMILARITY = clustering_config.min_title_similarity
MIN_TOPIC_OVERLAP = clustering_config.min_topic_overlap
COMBINED_THRESHOLD = clustering_config.combined_threshold

__all__ = [
    "ClusteringService",
    "compute_similarity",
    "compute_dedupe_key",
    "CLUSTER_WINDOW_HOURS",
    "MIN_ENTITY_OVERLAP",
    "MIN_TITLE_SIMILARITY",
    "MIN_TOPIC_OVERLAP",
    "COMBINED_THRESHOLD",
]
