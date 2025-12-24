"""
Clustering module.

Provides content clustering functionality for grouping related stories.
"""

# Core similarity functions (no database dependencies)
from app.clustering.similarity import (
    compute_similarity,
    compute_entity_overlap,
    compute_title_similarity,
    compute_topic_overlap,
)
from app.clustering.dedupe import compute_dedupe_key

# Service class (requires database dependencies)
try:
    from app.clustering.service import ClusteringService
except ImportError:
    ClusteringService = None  # type: ignore

__all__ = [
    "ClusteringService",
    "compute_similarity",
    "compute_entity_overlap",
    "compute_title_similarity",
    "compute_topic_overlap",
    "compute_dedupe_key",
]
