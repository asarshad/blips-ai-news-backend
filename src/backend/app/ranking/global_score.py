"""
Global score computation.

Combines all scoring signals into a single ranking score.

Formula:
    global_score = (
        quality_weight * quality_score +
        trend_weight * trend_score +
        recency_weight * recency_score +
        diversity_weight * diversity_boost  # Note: boost can be negative
    )

The global score is used for default feed ordering when no
personalization is applied.
"""

from typing import Optional

from app.config.scoring import scoring_weights


def compute_global_score(
    quality_score: float,
    trend_score: float,
    recency_score: float,
    diversity_boost: float = 0.0,
    quality_weight: Optional[float] = None,
    trend_weight: Optional[float] = None,
    recency_weight: Optional[float] = None,
    diversity_weight: Optional[float] = None,
) -> float:
    """
    Compute the global ranking score for content.
    
    Combines quality, trend, recency, and diversity signals.
    
    Args:
        quality_score: Source quality and completeness (0-1)
        trend_score: Viral/hot signal (0-1)
        recency_score: Freshness signal (0-1)
        diversity_boost: Diversity modifier (typically -0.5 to 0)
        quality_weight: Override quality weight
        trend_weight: Override trend weight
        recency_weight: Override recency weight
        diversity_weight: Override diversity weight
        
    Returns:
        Global score (typically 0-1, can be slightly negative with penalty)
    """
    # Use config defaults if not overridden
    if quality_weight is None:
        quality_weight = scoring_weights.quality
    if trend_weight is None:
        trend_weight = scoring_weights.trend
    if recency_weight is None:
        recency_weight = scoring_weights.recency
    if diversity_weight is None:
        diversity_weight = scoring_weights.diversity
    
    global_score = (
        quality_weight * quality_score +
        trend_weight * trend_score +
        recency_weight * recency_score +
        diversity_weight * diversity_boost
    )
    
    # Clamp to reasonable range
    return max(0.0, min(1.0, global_score))


def explain_global_score(
    quality_score: float,
    trend_score: float,
    recency_score: float,
    diversity_boost: float = 0.0,
) -> dict:
    """
    Return a breakdown of global score computation.
    
    Useful for debugging and understanding why content ranks
    where it does.
    
    Args:
        quality_score: Source quality and completeness (0-1)
        trend_score: Viral/hot signal (0-1)
        recency_score: Freshness signal (0-1)
        diversity_boost: Diversity modifier
        
    Returns:
        Dictionary with component breakdown
    """
    quality_contrib = scoring_weights.quality * quality_score
    trend_contrib = scoring_weights.trend * trend_score
    recency_contrib = scoring_weights.recency * recency_score
    diversity_contrib = scoring_weights.diversity * diversity_boost
    
    global_score = compute_global_score(
        quality_score, trend_score, recency_score, diversity_boost
    )
    
    return {
        "global_score": round(global_score, 4),
        "components": {
            "quality": {
                "raw_score": round(quality_score, 4),
                "weight": scoring_weights.quality,
                "contribution": round(quality_contrib, 4),
            },
            "trend": {
                "raw_score": round(trend_score, 4),
                "weight": scoring_weights.trend,
                "contribution": round(trend_contrib, 4),
            },
            "recency": {
                "raw_score": round(recency_score, 4),
                "weight": scoring_weights.recency,
                "contribution": round(recency_contrib, 4),
            },
            "diversity": {
                "raw_score": round(diversity_boost, 4),
                "weight": scoring_weights.diversity,
                "contribution": round(diversity_contrib, 4),
            },
        },
    }
