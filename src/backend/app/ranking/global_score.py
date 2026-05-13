"""
Global score computation.

Combines all scoring signals into a single ranking score.

Formula:
    global_score = (
        quality_weight * quality_score +
        trend_weight * trend_score +
        recency_weight * recency_score +
        diversity_weight * diversity_boost  # Note: boost can be negative
    ) + editorial_boost * EDITORIAL_BOOST_WEIGHT

The global score is used for default feed ordering when no
personalization is applied.
"""

import os
from typing import Optional

from app.config.scoring import scoring_weights

# Configurable via env – intentionally small to prevent editorial
# boost from completely overriding organic relevance signals.
# At the default 0.05 a max-boost (3) adds 0.15 to the [0-1] score.
EDITORIAL_BOOST_WEIGHT: float = float(os.getenv("EDITORIAL_BOOST_WEIGHT", "0.05"))

# Additive bonus applied to items the major-news LLM classifier confirmed
# as significant tech news (is_major_tech_news=True). Sized to lift confirmed
# major stories (~0.61 baseline) above typical filler (~0.55 ceiling) without
# crowding out all organic signals.
MAJOR_NEWS_SCORE_BOOST: float = float(os.getenv("MAJOR_NEWS_SCORE_BOOST", "0.12"))


def compute_global_score(
    quality_score: float,
    trend_score: float,
    recency_score: float,
    diversity_boost: float = 0.0,
    editorial_boost: int = 0,
    is_major_tech_news: bool = False,
    quality_weight: Optional[float] = None,
    trend_weight: Optional[float] = None,
    recency_weight: Optional[float] = None,
    diversity_weight: Optional[float] = None,
) -> float:
    """
    Compute the global ranking score for content.

    Combines quality, trend, recency, diversity, and editorial signals.

    Args:
        quality_score: Source quality and completeness (0-1)
        trend_score: Viral/hot signal (0-1)
        recency_score: Freshness signal (0-1)
        diversity_boost: Diversity modifier (typically -0.5 to 0)
        editorial_boost: Manual editorial importance (0-3)
        is_major_tech_news: LLM-confirmed significant tech-news item
        quality_weight: Override quality weight
        trend_weight: Override trend weight
        recency_weight: Override recency weight
        diversity_weight: Override diversity weight

    Returns:
        Global score (typically 0-1, can exceed 1.0 with editorial/major-news boost)
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

    base_score = (
        quality_weight * quality_score
        + trend_weight * trend_score
        + recency_weight * recency_score
        + diversity_weight * diversity_boost
    )

    # Editorial boost is additive – it nudges but does not dominate.
    editorial_addition = editorial_boost * EDITORIAL_BOOST_WEIGHT

    # Major-news boost: LLM-confirmed significant stories surface above filler.
    major_news_addition = MAJOR_NEWS_SCORE_BOOST if is_major_tech_news else 0.0

    global_score = base_score + editorial_addition + major_news_addition

    # Clamp to reasonable range (allow headroom for both boosts at max)
    return max(0.0, min(1.0 + 3 * EDITORIAL_BOOST_WEIGHT + MAJOR_NEWS_SCORE_BOOST, global_score))


def explain_global_score(
    quality_score: float,
    trend_score: float,
    recency_score: float,
    diversity_boost: float = 0.0,
    editorial_boost: int = 0,
    is_major_tech_news: bool = False,
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
        editorial_boost: Manual editorial importance (0-3)
        is_major_tech_news: LLM-confirmed significant tech-news item

    Returns:
        Dictionary with component breakdown
    """
    quality_contrib = scoring_weights.quality * quality_score
    trend_contrib = scoring_weights.trend * trend_score
    recency_contrib = scoring_weights.recency * recency_score
    diversity_contrib = scoring_weights.diversity * diversity_boost
    editorial_contrib = editorial_boost * EDITORIAL_BOOST_WEIGHT
    major_news_contrib = MAJOR_NEWS_SCORE_BOOST if is_major_tech_news else 0.0

    global_score = compute_global_score(
        quality_score,
        trend_score,
        recency_score,
        diversity_boost,
        editorial_boost=editorial_boost,
        is_major_tech_news=is_major_tech_news,
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
            "editorial": {
                "boost_level": editorial_boost,
                "weight": EDITORIAL_BOOST_WEIGHT,
                "contribution": round(editorial_contrib, 4),
            },
            "major_news": {
                "is_major": is_major_tech_news,
                "boost": MAJOR_NEWS_SCORE_BOOST,
                "contribution": round(major_news_contrib, 4),
            },
        },
    }
