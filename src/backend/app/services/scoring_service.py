"""
Scoring service for content ranking.

DEPRECATED: This module has been refactored into app.ranking

The functionality has been split into:
- app.ranking.quality - Quality score computation
- app.ranking.recency - Recency score with decay
- app.ranking.trend - Trend score from clusters
- app.ranking.diversity - Diversity boost computation
- app.ranking.global_score - Combined scoring
- app.ranking.service - ScoringService class

This file remains for backward compatibility. Import from app.ranking instead.
"""

# Re-export from new location for backward compatibility
from app.ranking import ScoringService
from app.ranking.quality import compute_source_weight as get_source_quality_weight

# Re-export config for tests that import directly
from app.config.scoring import (
    scoring_weights,
    SOURCE_QUALITY_WEIGHTS,
    ENGAGEMENT_WEIGHTS,
    recency_config,
    diversity_config,
)

# Create dict format for backward compatibility
SCORE_WEIGHTS = {
    "quality": scoring_weights.quality,
    "trend": scoring_weights.trend,
    "recency": scoring_weights.recency,
    "diversity": scoring_weights.diversity,
}

# Expose config values for backward compatibility
RECENCY_HALF_LIFE_HOURS = recency_config.half_life_hours
MAX_RECENCY_AGE_HOURS = recency_config.max_age_hours
MAX_TOPIC_DOMINANCE = diversity_config.max_topic_dominance

__all__ = [
    "ScoringService",
    "get_source_quality_weight",
    "SCORE_WEIGHTS",
    "SOURCE_QUALITY_WEIGHTS",
    "ENGAGEMENT_WEIGHTS",
    "RECENCY_HALF_LIFE_HOURS",
    "MAX_RECENCY_AGE_HOURS",
    "MAX_TOPIC_DOMINANCE",
]
