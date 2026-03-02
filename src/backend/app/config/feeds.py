"""
RSS feed sources configuration.

Defines all RSS feeds to fetch for article ingestion.
"""

from typing import List


class FeedSource:
    """Represents a single RSS feed source."""
    
    def __init__(
        self,
        name: str,
        url: str,
        category: str = "Tech",
        quality_weight: float = 0.5,
        enabled: bool = True
    ):
        self.name = name
        self.url = url
        self.category = category
        self.quality_weight = quality_weight
        self.enabled = enabled


# Premium tech publications
PREMIUM_FEEDS: List[FeedSource] = [
    FeedSource(
        name="TechCrunch",
        url="https://techcrunch.com/feed/",
        quality_weight=0.90,
    ),
    FeedSource(
        name="The Verge",
        url="https://www.theverge.com/rss/index.xml",
        quality_weight=0.90,
    ),
    FeedSource(
        name="Wired",
        url="https://www.wired.com/feed/rss",
        quality_weight=0.85,
    ),
    FeedSource(
        name="Ars Technica",
        url="https://feeds.arstechnica.com/arstechnica/technology-lab",
        quality_weight=0.90,
    ),
    FeedSource(
        name="MIT Technology Review",
        url="https://www.technologyreview.com/feed/",
        quality_weight=0.95,
    ),
]

# Major tech outlets
MAJOR_FEEDS: List[FeedSource] = [
    FeedSource(
        name="Engadget",
        url="https://www.engadget.com/rss.xml",
        quality_weight=0.75,
    ),
    FeedSource(
        name="CNET",
        url="https://www.cnet.com/rss/all/",
        quality_weight=0.75,
    ),
    FeedSource(
        name="ZDNet",
        url="https://www.zdnet.com/rss.xml",
        quality_weight=0.75,
    ),
    FeedSource(
        name="VentureBeat",
        url="https://venturebeat.com/feed/",
        quality_weight=0.80,
    ),
]

# AI-focused feeds
AI_FEEDS: List[FeedSource] = [
    FeedSource(
        name="AI News",
        url="https://www.artificialintelligence-news.com/feed/",
        category="AI",
        quality_weight=0.70,
    ),
]


def get_all_feeds() -> List[FeedSource]:
    """Get all enabled RSS feed sources."""
    all_feeds = PREMIUM_FEEDS + MAJOR_FEEDS + AI_FEEDS
    return [f for f in all_feeds if f.enabled]


def get_feed_urls() -> List[str]:
    """Get list of all enabled RSS feed URLs."""
    return [f.url for f in get_all_feeds()]


def get_feeds_by_category(category: str) -> List[FeedSource]:
    """Get feeds filtered by category."""
    return [f for f in get_all_feeds() if f.category == category]
