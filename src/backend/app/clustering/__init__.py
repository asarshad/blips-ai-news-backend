"""
Clustering module.

Provides content clustering functionality for grouping related stories.
"""

from importlib import import_module

# Core similarity functions (no database dependencies)
from app.clustering.dedupe import compute_dedupe_key
from app.clustering.similarity import (
    compute_entity_overlap,
    compute_similarity,
    compute_title_similarity,
    compute_topic_overlap,
)

__all__ = [
    "ClusteringService",
    "compute_similarity",
    "compute_entity_overlap",
    "compute_title_similarity",
    "compute_topic_overlap",
    "compute_dedupe_key",
]


def __getattr__(name):
    if name == "ClusteringService":
        value = getattr(import_module("app.clustering.service"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
