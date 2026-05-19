"""
Scoring configuration and tunable parameters.

All scoring-related constants live here. No magic numbers in business logic.

The global ranking formula:
    global_score = (
        WEIGHTS['quality'] * quality_score +
        WEIGHTS['trend'] * trend_score +
        WEIGHTS['recency'] * recency_score +
        WEIGHTS['diversity'] * diversity_boost
    )

Tuning Guidelines:
    - Increase quality weight for more editorial control
    - Increase trend weight to favor viral content
    - Increase recency weight for faster content turnover
    - Increase diversity weight to reduce topic dominance
"""

from typing import Dict

from pydantic import Field
from pydantic_settings import BaseSettings


class ScoringWeights(BaseSettings):
    """
    Weights for the global score formula.

    Must sum to 1.0 for normalized output.
    """

    quality: float = Field(
        default=0.50, ge=0.0, le=1.0, description="Weight for source quality and completeness"
    )
    trend: float = Field(
        default=0.20, ge=0.0, le=1.0, description="Weight for trending/engagement signals"
    )
    recency: float = Field(default=0.20, ge=0.0, le=1.0, description="Weight for content freshness")
    diversity: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Weight for diversity boost/penalty"
    )

    class Config:
        env_prefix = "SCORING_WEIGHT_"

    def validate_sum(self) -> bool:
        """Verify weights sum to 1.0."""
        total = self.quality + self.trend + self.recency + self.diversity
        return abs(total - 1.0) < 0.01


class RecencyConfig(BaseSettings):
    """
    Configuration for recency score calculation.

    Uses exponential decay: score = 2^(-hours_old / half_life)

    At half_life hours, score = 0.5
    At 2*half_life hours, score = 0.25
    """

    half_life_hours: int = Field(
        default=24, ge=1, le=168, description="Hours until score drops to 50%"
    )
    max_age_hours: int = Field(
        default=168, ge=24, le=720, description="Max age to consider (7 days)"
    )
    min_score: float = Field(default=0.01, ge=0.0, le=0.5, description="Floor for recency score")

    class Config:
        env_prefix = "RECENCY_"


class DiversityConfig(BaseSettings):
    """
    Configuration for diversity scoring.

    Penalizes over-representation of single topics in feed.
    """

    max_topic_dominance: float = Field(
        default=0.40, ge=0.1, le=0.8, description="Max % of feed from one topic"
    )
    penalty_factor: float = Field(
        default=0.5, ge=0.0, le=1.0, description="How much to penalize dominant topics"
    )

    class Config:
        env_prefix = "DIVERSITY_"


class TrendConfig(BaseSettings):
    """
    Configuration for trend score calculation.

    Trend score = cluster_weight * cluster_score + engagement_weight * engagement_score

    Note: Keep engagement_weight low (<30%) in early-stage products
    to prevent gaming and ensure quality content surfaces.
    """

    cluster_weight: float = Field(
        default=0.80, ge=0.0, le=1.0, description="Weight for cluster size signal"
    )
    engagement_weight: float = Field(
        default=0.20, ge=0.0, le=1.0, description="Weight for user engagement (keep low initially)"
    )

    class Config:
        env_prefix = "TREND_"


# Source Quality Weights
# 0.0 = lowest quality, 1.0 = highest quality
# These influence the quality_score component
# Note: RSS feeds now use base_quality_weight from FeedConfig,
# but these remain as fallback for legacy code paths.
SOURCE_QUALITY_WEIGHTS: Dict[str, float] = {
    # Premium tech publications (0.85-0.95)
    "mit technology review": 0.95,
    "ieee spectrum": 0.92,
    "krebs on security": 0.92,
    "ars technica": 0.90,
    "techcrunch": 0.90,
    "the verge": 0.90,
    "the atlantic": 0.88,
    "wired": 0.85,
    "the new stack": 0.85,
    "infoq": 0.72,  # demoted: broad INFRA coverage but too much niche-language content
    "cnbc technology": 0.88,
    "platformer": 0.90,
    "big technology": 0.88,
    "fabricated knowledge": 0.90,
    "benedict evans": 0.88,
    "the information": 0.92,
    "bloomberg": 0.92,
    "reuters": 0.90,
    "bbc": 0.85,
    "financial times": 0.92,
    # Company official blogs (0.70-0.95 - down-ranked for primary sources)
    "google": 0.70,
    "microsoft": 0.70,
    "apple": 0.70,
    "openai": 0.70,
    "anthropic": 0.70,
    "meta": 0.70,
    "aws": 0.80,
    "google cloud": 0.80,
    # Major tech outlets (0.70-0.80)
    "venturebeat": 0.80,
    "smashing magazine": 0.80,
    "css-tricks": 0.78,
    "the hacker news": 0.78,
    "engadget": 0.75,
    "cnet": 0.75,
    "zdnet": 0.75,
    "crunchbase": 0.75,
    "pitchbook": 0.75,
    "dark reading": 0.75,
    "hacker news": 0.70,
    "mashable": 0.70,
    "android authority": 0.74,
    "the register": 0.85,
    "bleeping computer": 0.82,
    "digital trends": 0.73,
    "xda developers": 0.73,
    "techradar": 0.74,
    "infoworld": 0.78,
    "404 media": 0.84,
    "tom's guide": 0.74,
    "tom's hardware": 0.80,
    "9to5mac": 0.78,
    "9to5google": 0.76,
    "android central": 0.76,
    "anandtech": 0.88,
    "macrumors": 0.78,
    # YouTube creators (quality varies, 0.70-0.90)
    "mkbhd": 0.90,
    "dave2d": 0.85,
    "linus tech tips": 0.80,
    "austin evans": 0.75,
    "unbox therapy": 0.70,
    # Default for unknown sources
    "default": 0.35,
}


# Engagement Event Weights
# Used to compute engagement component of trend score
# Higher weight = stronger signal of quality/interest
# Keys are string event type names to avoid circular imports with models
ENGAGEMENT_WEIGHTS: Dict[str, int] = {
    "view_10s": 1,  # Mild interest
    "open_source": 3,  # Strong interest
    "share": 5,  # Endorsement
    "save": 5,  # Bookmarked for later
    "chat_start": 6,  # Deep engagement
    "chat_message": 1,  # Continued engagement
    "video_impression": 1,
    "video_start": 2,
    "video_3s": 2,
    "video_50pct": 4,
    "video_95pct": 6,
    "video_skip_lt_2s": -2,
    "video_save": 5,
    "video_share": 5,
    "less_from_creator": -5,
    "caught_up": 0,
}


def get_source_quality(source: str) -> float:
    """
    Get quality weight for a source.

    Args:
        source: Source name (will be lowercased)

    Returns:
        Quality weight between 0.0 and 1.0
    """
    normalized = source.lower().strip()
    return SOURCE_QUALITY_WEIGHTS.get(normalized, SOURCE_QUALITY_WEIGHTS["default"])


def get_engagement_weight(event_type: str) -> int:
    """
    Get weight for an engagement event type.

    Args:
        event_type: The type of engagement event (string name)

    Returns:
        Integer weight for the event
    """
    # Handle both string and enum types
    key = event_type.value if hasattr(event_type, "value") else str(event_type).lower()
    return ENGAGEMENT_WEIGHTS.get(key, 1)


# Default instances for convenience
scoring_weights = ScoringWeights()
recency_config = RecencyConfig()
diversity_config = DiversityConfig()
trend_config = TrendConfig()
