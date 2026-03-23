"""
Ranking domain module.

Implements content scoring and ranking algorithms.
"""

from importlib import import_module

# Core scoring functions (no database dependencies)
from app.ranking.diversity import compute_diversity_boost
from app.ranking.global_score import compute_global_score
from app.ranking.quality import compute_quality_score, compute_source_weight
from app.ranking.recency import compute_recency_score
from app.ranking.trend import compute_trend_score

__all__ = [
    "compute_quality_score",
    "compute_source_weight",
    "compute_recency_score",
    "compute_trend_score",
    "compute_diversity_boost",
    "compute_global_score",
    "ScoringService",
]


def __getattr__(name):
    if name == "ScoringService":
        value = getattr(import_module("app.ranking.service"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
