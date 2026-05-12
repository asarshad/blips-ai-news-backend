"""
Clustering configuration and tunable parameters.

Controls how content items are grouped into clusters (same story).
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class ClusteringConfig(BaseSettings):
    """
    Configuration for content clustering.

    Clustering groups content about the same story to prevent
    showing duplicate coverage in user feeds.

    Similarity Formula:
        combined_score = (
            entity_weight * entity_overlap +
            title_weight * title_similarity +
            topic_weight * topic_overlap
        )

        if combined_score >= threshold: cluster together
    """

    # Time window
    window_hours: int = Field(
        default=48, ge=12, le=168, description="Time window for clustering (hours)"
    )

    # Similarity thresholds
    min_entity_overlap: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Min entity overlap to consider"
    )
    min_title_similarity: float = Field(
        default=0.6, ge=0.0, le=1.0, description="Min title similarity to consider"
    )
    min_topic_overlap: float = Field(
        default=0.4, ge=0.0, le=1.0, description="Min topic overlap to consider"
    )
    combined_threshold: float = Field(
        default=0.4, ge=0.0, le=1.0, description="Combined score threshold for clustering"
    )

    # Similarity weights (should sum to 1.0)
    entity_weight: float = Field(
        default=0.40, ge=0.0, le=1.0, description="Weight for entity overlap"
    )
    title_weight: float = Field(
        default=0.40, ge=0.0, le=1.0, description="Weight for title similarity"
    )
    topic_weight: float = Field(
        default=0.20, ge=0.0, le=1.0, description="Weight for topic overlap"
    )

    # Processing limits
    max_candidates: int = Field(default=100, ge=10, le=500, description="Max candidates to compare")
    batch_size: int = Field(
        default=500, ge=50, le=2000, description="Batch size for clustering job"
    )

    class Config:
        env_prefix = "CLUSTERING_"


# Default instance
clustering_config = ClusteringConfig()
