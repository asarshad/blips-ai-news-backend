"""
Ranking domain module.

Implements content scoring and ranking algorithms.
"""

# Core scoring functions (no database dependencies)
from app.ranking.quality import compute_quality_score, compute_source_weight
from app.ranking.recency import compute_recency_score
from app.ranking.trend import compute_trend_score
from app.ranking.diversity import compute_diversity_boost
from app.ranking.global_score import compute_global_score

# Service class (requires database dependencies)
# Import separately to avoid issues when DB is not configured
try:
    from app.ranking.service import ScoringService
except ImportError:
    ScoringService = None  # type: ignore

__all__ = [
    "compute_quality_score",
    "compute_source_weight",
    "compute_recency_score",
    "compute_trend_score",
    "compute_diversity_boost",
    "compute_global_score",
    "ScoringService",
]
