"""
Diversity configuration for feed mixing.

Configures source and topic diversity constraints for different surfaces
(articles, videos, reels). These settings ensure users see a varied mix
of content sources rather than being dominated by a single publisher.
"""

from typing import Dict, Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from dataclasses import dataclass, field
import os


@dataclass
class DiversityConstraints:
    """
    Diversity constraints for a specific feed surface.
    
    Attributes:
        window_size: Rolling window size for constraint checking (N items)
        max_source_per_window: Max items from same source in window (K items)
        max_topic_per_window: Max items with same primary topic in window (optional)
        allow_consecutive_same_source: Hard constraint - no back-to-back same source
        min_inventory_for_constraints: Minimum inventory to enforce constraints
        relaxation_steps: Ordered steps to relax constraints when stuck
    """
    window_size: int = 5
    max_source_per_window: int = 2
    max_topic_per_window: Optional[int] = None  # None = no topic cap
    allow_consecutive_same_source: bool = False
    min_inventory_for_constraints: int = 10
    relaxation_steps: list = field(default_factory=lambda: [
        {"allow_consecutive": True},
        {"max_source_per_window": 3},
        {"max_source_per_window": 4},
        {"disable_all": True}
    ])


# Default configurations for each surface
DEFAULT_ARTICLE_DIVERSITY = DiversityConstraints(
    window_size=5,
    max_source_per_window=2,
    max_topic_per_window=3,
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=8,
)

DEFAULT_VIDEO_DIVERSITY = DiversityConstraints(
    window_size=5,
    max_source_per_window=2,
    max_topic_per_window=None,  # Videos often have less topic diversity
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=6,
)

DEFAULT_REEL_DIVERSITY = DiversityConstraints(
    window_size=4,
    max_source_per_window=1,  # Stricter for reels - problem area
    max_topic_per_window=2,
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=5,
)


class DiversitySettings(BaseSettings):
    """
    Diversity settings loaded from environment variables.
    
    Environment variables:
        DIVERSITY_ARTICLES_WINDOW_SIZE: Window size for articles (default: 5)
        DIVERSITY_ARTICLES_MAX_SOURCE: Max source per window for articles (default: 2)
        DIVERSITY_ARTICLES_MAX_TOPIC: Max topic per window for articles (default: 3)
        
        DIVERSITY_VIDEOS_WINDOW_SIZE: Window size for videos (default: 5)
        DIVERSITY_VIDEOS_MAX_SOURCE: Max source per window for videos (default: 2)
        
        DIVERSITY_REELS_WINDOW_SIZE: Window size for reels (default: 4)
        DIVERSITY_REELS_MAX_SOURCE: Max source per window for reels (default: 1)
        DIVERSITY_REELS_MAX_TOPIC: Max topic per window for reels (default: 2)
        
        DIVERSITY_ENABLED: Enable/disable diversity mixing (default: true)
    """
    
    # Global enable flag
    enabled: bool = Field(default=True, description="Enable diversity mixing")
    
    # Article settings
    articles_window_size: int = Field(default=5)
    articles_max_source: int = Field(default=2)
    articles_max_topic: Optional[int] = Field(default=3)
    articles_allow_consecutive: bool = Field(default=False)
    articles_min_inventory: int = Field(default=8)
    
    # Video settings
    videos_window_size: int = Field(default=5)
    videos_max_source: int = Field(default=2)
    videos_max_topic: Optional[int] = Field(default=None)
    videos_allow_consecutive: bool = Field(default=False)
    videos_min_inventory: int = Field(default=6)
    
    # Reel settings
    reels_window_size: int = Field(default=4)
    reels_max_source: int = Field(default=1)
    reels_max_topic: Optional[int] = Field(default=2)
    reels_allow_consecutive: bool = Field(default=False)
    reels_min_inventory: int = Field(default=5)
    
    class Config:
        env_prefix = "DIVERSITY_"
        case_sensitive = False
    
    def get_article_constraints(self) -> DiversityConstraints:
        """Get diversity constraints for articles."""
        return DiversityConstraints(
            window_size=self.articles_window_size,
            max_source_per_window=self.articles_max_source,
            max_topic_per_window=self.articles_max_topic,
            allow_consecutive_same_source=self.articles_allow_consecutive,
            min_inventory_for_constraints=self.articles_min_inventory,
        )
    
    def get_video_constraints(self) -> DiversityConstraints:
        """Get diversity constraints for videos."""
        return DiversityConstraints(
            window_size=self.videos_window_size,
            max_source_per_window=self.videos_max_source,
            max_topic_per_window=self.videos_max_topic,
            allow_consecutive_same_source=self.videos_allow_consecutive,
            min_inventory_for_constraints=self.videos_min_inventory,
        )
    
    def get_reel_constraints(self) -> DiversityConstraints:
        """Get diversity constraints for reels."""
        return DiversityConstraints(
            window_size=self.reels_window_size,
            max_source_per_window=self.reels_max_source,
            max_topic_per_window=self.reels_max_topic,
            allow_consecutive_same_source=self.reels_allow_consecutive,
            min_inventory_for_constraints=self.reels_min_inventory,
        )


# Singleton instance
_diversity_settings: Optional[DiversitySettings] = None


def get_diversity_settings() -> DiversitySettings:
    """Get diversity settings singleton."""
    global _diversity_settings
    if _diversity_settings is None:
        _diversity_settings = DiversitySettings()
    return _diversity_settings


def reset_diversity_settings():
    """Reset settings (for testing)."""
    global _diversity_settings
    _diversity_settings = None
