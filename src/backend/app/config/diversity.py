"""
Diversity configuration for feed mixing.

Configures source and topic diversity constraints for different surfaces
(articles, videos, reels). These settings ensure users see a varied mix
of content sources rather than being dominated by a single publisher.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


@dataclass
class CategoryCapConfig:
    """
    Per-category cap applied inside the rolling diversity window.

    Attributes:
        max_window_pct: Maximum fraction of the window that may be items whose
            primary topic matches this category (0.0–1.0). E.g. 0.40 means
            the AI category may contribute at most 40 % of any window.
        max_consecutive: Maximum number of back-to-back items from this
            category before the mixer must insert a different-category item.
    """

    max_window_pct: float = 1.0  # 1.0 = effectively no limit
    max_consecutive: int = 999  # 999 = effectively no limit


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
        per_category_minimums: Guaranteed minimum items per topic category in output.
        category_caps: Per-category window-percentage and consecutive caps.
            Keys must match the first element of ContentItem.topics exactly
            (case-sensitive). Example::

                {"AI": CategoryCapConfig(max_window_pct=0.40, max_consecutive=2)}
    """

    window_size: int = 5
    max_source_per_window: int = 2
    max_topic_per_window: Optional[int] = None  # None = no topic cap
    allow_consecutive_same_source: bool = False
    min_inventory_for_constraints: int = 10
    relaxation_steps: list = field(
        default_factory=lambda: [
            {"allow_consecutive": True},
            {"max_source_per_window": 3},
            {"max_source_per_window": 4},
            {"disable_all": True},
        ]
    )
    # Minimum items per topic category guaranteed in the output.
    # Keys match the first element of ContentItem.topics (case-sensitive).
    # E.g. {"AI": 1, "Security": 1} ensures at least one item from each.
    per_category_minimums: Dict[str, int] = field(default_factory=dict)
    # Per-category window caps (pct + consecutive limits).
    category_caps: Dict[str, CategoryCapConfig] = field(default_factory=dict)


# Default configurations for each surface
DEFAULT_ARTICLE_DIVERSITY = DiversityConstraints(
    window_size=5,
    max_source_per_window=2,
    max_topic_per_window=3,
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=8,
    per_category_minimums={"AI": 1, "Security": 1},
    # AI cap: at most 40 % of any window; never 3-in-a-row.
    category_caps={"AI": CategoryCapConfig(max_window_pct=0.40, max_consecutive=2)},
)

DEFAULT_VIDEO_DIVERSITY = DiversityConstraints(
    window_size=5,
    max_source_per_window=2,
    max_topic_per_window=None,  # Videos often have less topic diversity
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=6,
    category_caps={"AI": CategoryCapConfig(max_window_pct=0.40, max_consecutive=2)},
)

DEFAULT_REEL_DIVERSITY = DiversityConstraints(
    window_size=4,
    max_source_per_window=1,  # Stricter for reels - problem area
    max_topic_per_window=2,
    allow_consecutive_same_source=False,
    min_inventory_for_constraints=5,
    category_caps={"AI": CategoryCapConfig(max_window_pct=0.40, max_consecutive=2)},
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
            per_category_minimums=DEFAULT_ARTICLE_DIVERSITY.per_category_minimums,
            category_caps=DEFAULT_ARTICLE_DIVERSITY.category_caps,
        )

    def get_video_constraints(self) -> DiversityConstraints:
        """Get diversity constraints for videos."""
        return DiversityConstraints(
            window_size=self.videos_window_size,
            max_source_per_window=self.videos_max_source,
            max_topic_per_window=self.videos_max_topic,
            allow_consecutive_same_source=self.videos_allow_consecutive,
            min_inventory_for_constraints=self.videos_min_inventory,
            category_caps=DEFAULT_VIDEO_DIVERSITY.category_caps,
        )

    def get_reel_constraints(self) -> DiversityConstraints:
        """Get diversity constraints for reels."""
        return DiversityConstraints(
            window_size=self.reels_window_size,
            max_source_per_window=self.reels_max_source,
            max_topic_per_window=self.reels_max_topic,
            allow_consecutive_same_source=self.reels_allow_consecutive,
            min_inventory_for_constraints=self.reels_min_inventory,
            category_caps=DEFAULT_REEL_DIVERSITY.category_caps,
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
