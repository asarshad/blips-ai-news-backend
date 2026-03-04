"""
RSS feed sources configuration.

Defines all RSS feeds to fetch for article ingestion.
"""

from typing import List, Optional


class FeedSource:
    """Represents a single RSS feed source."""

    def __init__(
        self,
        name: str,
        url: str,
        category: str = "Tech",
        quality_weight: float = 0.5,
        enabled: bool = True,
        tier: str = "Standard",
        daily_cap: Optional[int] = None,
    ):
        self.name = name
        self.url = url
        self.category = category
        self.quality_weight = quality_weight
        self.enabled = enabled
        self.tier = tier  # "Premium" | "Standard" | "Supplemental" | "Research"
        self.daily_cap = daily_cap  # max items ingested per day (None = unlimited)


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

# AI-focused feeds — primary sources (official lab blogs)
AI_PRIMARY_FEEDS: List[FeedSource] = [
    FeedSource(
        name="OpenAI Blog",
        url="https://openai.com/news/rss.xml",
        category="AI",
        quality_weight=0.88,
        tier="Premium",
        daily_cap=2,
    ),
    FeedSource(
        name="Anthropic Blog",
        url="https://www.anthropic.com/rss.xml",
        category="AI",
        quality_weight=0.88,
        tier="Premium",
        daily_cap=2,
    ),
    FeedSource(
        name="Google DeepMind Blog",
        url="https://deepmind.google/blog/rss.xml",
        category="AI",
        quality_weight=0.92,
        tier="Premium",
        daily_cap=2,
    ),
    FeedSource(
        name="Meta AI Blog",
        url="https://ai.meta.com/blog/rss/",
        category="AI",
        quality_weight=0.85,
        tier="Premium",
        daily_cap=2,
    ),
    FeedSource(
        name="Microsoft AI Blog",
        url="https://blogs.microsoft.com/ai/feed/",
        category="AI",
        quality_weight=0.85,
        tier="Premium",
        daily_cap=2,
    ),
    FeedSource(
        name="Hugging Face Blog",
        url="https://huggingface.co/blog/feed.xml",
        category="AI",
        quality_weight=0.88,
        tier="Premium",
        daily_cap=2,
    ),
]

# AI-focused feeds — research publications and academic labs
AI_RESEARCH_FEEDS: List[FeedSource] = [
    FeedSource(
        name="Papers With Code",
        url="https://paperswithcode.com/rss",
        category="AI",
        quality_weight=0.80,
        tier="Research",
        daily_cap=2,
    ),
    FeedSource(
        name="Stanford HAI",
        url="https://hai.stanford.edu/news/feed",
        category="AI",
        quality_weight=0.90,
        tier="Research",
        daily_cap=1,
    ),
    FeedSource(
        name="MIT CSAIL",
        url="https://news.csail.mit.edu/feed/",
        category="AI",
        quality_weight=0.88,
        tier="Research",
        daily_cap=1,
    ),
]

# AI-focused feeds — infrastructure, hardware, cloud AI
AI_INFRA_FEEDS: List[FeedSource] = [
    FeedSource(
        name="NVIDIA AI Blog",
        url="https://blogs.nvidia.com/blog/category/artificial-intelligence/feed/",
        category="AI",
        quality_weight=0.78,
        tier="Standard",
        daily_cap=2,
    ),
    FeedSource(
        name="AWS Machine Learning Blog",
        url="https://aws.amazon.com/blogs/machine-learning/feed/",
        category="AI",
        quality_weight=0.75,
        tier="Standard",
        daily_cap=2,
    ),
    FeedSource(
        name="Azure AI Blog",
        url="https://techcommunity.microsoft.com/category/azure-ai-services/blog/AzureAIServicesBlog/rss",
        category="AI",
        quality_weight=0.75,
        tier="Standard",
        daily_cap=2,
    ),
    FeedSource(
        name="SemiAnalysis",
        url="https://www.semianalysis.com/feed",
        category="AI",
        quality_weight=0.90,
        tier="Premium",
        daily_cap=1,
    ),
]

# Legacy catch-all AI bucket (original entry kept for backward compat)
AI_FEEDS: List[FeedSource] = [
    FeedSource(
        name="AI News",
        url="https://www.artificialintelligence-news.com/feed/",
        category="AI",
        quality_weight=0.70,
        tier="Standard",
        daily_cap=2,
    ),
]


def get_all_feeds() -> List[FeedSource]:
    """Get all enabled RSS feed sources."""
    all_feeds = (
        PREMIUM_FEEDS
        + MAJOR_FEEDS
        + AI_PRIMARY_FEEDS
        + AI_RESEARCH_FEEDS
        + AI_INFRA_FEEDS
        + AI_FEEDS
    )
    return [f for f in all_feeds if f.enabled]


def get_ai_feeds() -> List[FeedSource]:
    """Get all enabled AI-category RSS feed sources."""
    return [f for f in get_all_feeds() if f.category == "AI"]


def get_feed_urls() -> List[str]:
    """Get list of all enabled RSS feed URLs."""
    return [f.url for f in get_all_feeds()]


def get_feeds_by_category(category: str) -> List[FeedSource]:
    """Get feeds filtered by category."""
    return [f for f in get_all_feeds() if f.category == category]
