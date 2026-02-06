"""
RSS Feed Configuration with Editorial Roles.

This module defines the complete feed registry with role-based metadata
for reliable, diverse tech news ingestion.

Target: ~35-40 high-quality, non-repetitive tech articles per day.

Architecture:
    - Each feed has an editorial ROLE (breaking, analysis, infra, etc.)
    - Quality tiers determine ranking weight modifiers
    - Per-feed daily caps prevent source dominance
    - Decay profiles control how quickly old articles lose ranking
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class FeedRole(Enum):
    """
    Editorial role defining the feed's purpose in the content mix.
    
    BREAKING: Fast-moving news, gadget launches, tech industry updates
    ANALYSIS: Deep dives, opinion, long-form context pieces
    INFRA: Cloud, DevOps, backend systems, enterprise tech
    SECURITY: Cybersecurity, privacy, threats, vulnerabilities
    BUSINESS: Startups, funding, acquisitions, market analysis
    DEV: Developer tools, tutorials, programming trends
    PRIMARY: Official company blogs (down-ranked unless corroborated)
    """
    BREAKING = "breaking"
    ANALYSIS = "analysis"
    INFRA = "infra"
    SECURITY = "security"
    BUSINESS = "business"
    DEV = "dev"
    PRIMARY = "primary"


class QualityTier(Enum):
    """
    Quality tier for ranking weight modifiers.
    
    PREMIUM: Top-tier publications with strong editorial standards
    STANDARD: Reliable sources with good coverage
    SUPPLEMENTAL: Niche or variable quality, useful for diversity
    """
    PREMIUM = "premium"
    STANDARD = "standard"
    SUPPLEMENTAL = "supplemental"


class DecayProfile(Enum):
    """
    Decay profile controlling how quickly articles lose ranking.
    
    FAST: Breaking news - stale quickly (half-life ~6 hours)
    NORMAL: Standard news cycle (half-life ~24 hours)
    SLOW: Analysis/evergreen - stays relevant longer (half-life ~48 hours)
    """
    FAST = "fast"
    NORMAL = "normal"
    SLOW = "slow"


# Quality tier weight modifiers
QUALITY_TIER_MODIFIERS: Dict[QualityTier, float] = {
    QualityTier.PREMIUM: 1.15,      # 15% boost
    QualityTier.STANDARD: 1.0,      # No modifier
    QualityTier.SUPPLEMENTAL: 0.85, # 15% reduction
}

# Decay profile half-life in hours
DECAY_HALF_LIFE_HOURS: Dict[DecayProfile, int] = {
    DecayProfile.FAST: 6,
    DecayProfile.NORMAL: 24,
    DecayProfile.SLOW: 48,
}

# Role quotas - target articles per day per role
ROLE_QUOTAS: Dict[FeedRole, int] = {
    FeedRole.BREAKING: 12,    # Major news coverage
    FeedRole.ANALYSIS: 8,     # Deep dives
    FeedRole.INFRA: 5,        # Cloud/backend
    FeedRole.SECURITY: 4,     # Security news
    FeedRole.BUSINESS: 4,     # Startups/funding
    FeedRole.DEV: 4,          # Developer content
    FeedRole.PRIMARY: 3,      # Official blogs (down-ranked)
}


@dataclass
class FeedConfig:
    """
    Configuration for a single RSS feed.
    
    Attributes:
        url: RSS feed URL
        name: Human-readable name for logging
        role: Editorial role (breaking, analysis, etc.)
        quality_tier: Quality tier for ranking modifiers
        daily_cap: Maximum articles per day from this feed
        decay_profile: How quickly articles lose ranking
        enabled: Whether this feed is active
        base_quality_weight: Override for source quality (0.0-1.0)
        notes: Internal documentation
    """
    url: str
    name: str
    role: FeedRole
    quality_tier: QualityTier = QualityTier.STANDARD
    daily_cap: int = 3
    decay_profile: DecayProfile = DecayProfile.NORMAL
    enabled: bool = True
    base_quality_weight: Optional[float] = None
    notes: str = ""


# =============================================================================
# FEED REGISTRY
# =============================================================================
# Organized by editorial role.
# Total daily capacity: ~70 articles (with caps)
# Target after deduplication: ~35-40 articles/day

FEED_REGISTRY: List[FeedConfig] = [
    # =========================================================================
    # BREAKING NEWS
    # Fast-moving tech news, product launches, industry updates
    # Target: 10-12 articles/day
    # =========================================================================
    FeedConfig(
        url="https://techcrunch.com/feed/",
        name="TechCrunch",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=4,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.90,
        notes="Top tech news, startup coverage, product launches",
    ),
    FeedConfig(
        url="https://www.theverge.com/rss/index.xml",
        name="The Verge",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=4,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.90,
        notes="Consumer tech, gadgets, digital culture",
    ),
    FeedConfig(
        url="https://www.engadget.com/rss.xml",
        name="Engadget",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Gadget news and reviews",
    ),
    FeedConfig(
        url="https://www.cnet.com/rss/news/",
        name="CNET",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Consumer tech news and reviews",
    ),
    FeedConfig(
        url="https://www.zdnet.com/news/rss.xml",
        name="ZDNet",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Enterprise and consumer tech news",
    ),
    FeedConfig(
        url="https://feeds.arstechnica.com/arstechnica/technology-lab",
        name="Ars Technica",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.90,
        notes="In-depth tech journalism, science, policy",
    ),

    # =========================================================================
    # ANALYSIS & CONTEXT
    # Deep dives, opinion, long-form journalism
    # Target: 6-8 articles/day
    # =========================================================================
    FeedConfig(
        url="https://www.wired.com/feed/rss",
        name="Wired",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.85,
        notes="Tech culture, long-form features, analysis",
    ),
    FeedConfig(
        url="https://www.technologyreview.com/feed/",
        name="MIT Technology Review",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.95,
        notes="Academic rigor, emerging tech, AI research",
    ),
    FeedConfig(
        url="https://spectrum.ieee.org/feeds/feed.rss",
        name="IEEE Spectrum",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.92,
        notes="Engineering perspective, technical depth",
    ),
    FeedConfig(
        url="https://www.theatlantic.com/feed/channel/technology/",
        name="The Atlantic (Tech)",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.88,
        notes="Tech policy, society, long-form analysis",
    ),

    # =========================================================================
    # INFRASTRUCTURE & CLOUD
    # Cloud computing, DevOps, backend systems, enterprise
    # Target: 4-5 articles/day
    # =========================================================================
    FeedConfig(
        url="https://thenewstack.io/feed/",
        name="The New Stack",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.85,
        notes="Cloud native, Kubernetes, DevOps",
    ),
    FeedConfig(
        url="https://www.infoq.com/feed/",
        name="InfoQ",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.85,
        notes="Software architecture, enterprise patterns",
    ),
    FeedConfig(
        url="https://aws.amazon.com/blogs/aws/feed/",
        name="AWS Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="AWS announcements and tutorials",
    ),
    FeedConfig(
        url="https://cloudblog.withgoogle.com/rss/",
        name="Google Cloud Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="GCP announcements and best practices",
    ),

    # =========================================================================
    # STARTUPS & BUSINESS
    # Funding, acquisitions, market analysis, VC
    # Target: 3-4 articles/day
    # =========================================================================
    FeedConfig(
        url="https://venturebeat.com/feed/",
        name="VentureBeat",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="AI, enterprise tech, gaming industry",
    ),
    FeedConfig(
        url="https://news.crunchbase.com/feed/",
        name="Crunchbase News",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.75,
        notes="Startup funding, valuations, M&A",
    ),
    # NOTE: PitchBook has Cloudflare protection that blocks automated requests
    # FeedConfig(
    #     url="https://pitchbook.com/news/rss",
    #     name="PitchBook",
    #     role=FeedRole.BUSINESS,
    #     quality_tier=QualityTier.STANDARD,
    #     daily_cap=2,
    #     decay_profile=DecayProfile.NORMAL,
    #     base_quality_weight=0.75,
    #     notes="VC/PE deals, market data",
    # ),

    # =========================================================================
    # SECURITY & PRIVACY
    # Cybersecurity, threats, vulnerabilities, privacy
    # Target: 3-4 articles/day
    # =========================================================================
    FeedConfig(
        url="https://krebsonsecurity.com/feed/",
        name="Krebs on Security",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.92,
        notes="Investigative security journalism",
    ),
    FeedConfig(
        url="https://feeds.feedburner.com/TheHackersNews",
        name="The Hacker News",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.STANDARD,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.78,
        notes="Security news, vulnerabilities, breaches",
    ),
    FeedConfig(
        url="https://www.darkreading.com/rss.xml",
        name="Dark Reading",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.75,
        notes="Enterprise security, threat intelligence",
    ),

    # =========================================================================
    # DEVELOPER PERSPECTIVE
    # Programming, tools, tutorials, developer culture
    # Target: 3-4 articles/day
    # =========================================================================
    FeedConfig(
        url="https://news.ycombinator.com/rss",
        name="Hacker News",
        role=FeedRole.DEV,
        quality_tier=QualityTier.SUPPLEMENTAL,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.70,
        notes="Community-driven, variable quality, high signal",
    ),
    FeedConfig(
        url="https://www.smashingmagazine.com/feed/",
        name="Smashing Magazine",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.80,
        notes="Web development, design, UX",
    ),
    FeedConfig(
        url="https://css-tricks.com/feed/",
        name="CSS-Tricks",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.78,
        notes="Frontend development, CSS, web tech",
    ),

    # =========================================================================
    # PRIMARY SOURCES (Official Blogs)
    # Company announcements - down-rank unless corroborated by news
    # Target: 2-3 articles/day
    # =========================================================================
    FeedConfig(
        url="https://openai.com/blog/rss.xml",
        name="OpenAI Blog",
        role=FeedRole.PRIMARY,
        quality_tier=QualityTier.SUPPLEMENTAL,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.70,  # Down-ranked
        notes="Official announcements - prefer news coverage",
    ),
    FeedConfig(
        url="https://blog.google/technology/ai/rss/",
        name="Google AI Blog",
        role=FeedRole.PRIMARY,
        quality_tier=QualityTier.SUPPLEMENTAL,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.70,  # Down-ranked
        notes="Official announcements - prefer news coverage",
    ),
    FeedConfig(
        url="https://blogs.microsoft.com/feed/",
        name="Microsoft Blog",
        role=FeedRole.PRIMARY,
        quality_tier=QualityTier.SUPPLEMENTAL,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.70,  # Down-ranked
        notes="Official announcements - prefer news coverage",
    ),
    FeedConfig(
        url="https://www.apple.com/newsroom/rss-feed.rss",
        name="Apple Newsroom",
        role=FeedRole.PRIMARY,
        quality_tier=QualityTier.SUPPLEMENTAL,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.70,  # Down-ranked
        notes="Official announcements - prefer news coverage",
    ),
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_enabled_feeds() -> List[FeedConfig]:
    """Get all enabled feed configurations."""
    return [f for f in FEED_REGISTRY if f.enabled]


def get_feeds_by_role(role: FeedRole) -> List[FeedConfig]:
    """Get all enabled feeds for a specific role."""
    return [f for f in FEED_REGISTRY if f.enabled and f.role == role]


def get_feed_urls() -> List[str]:
    """Get list of all enabled feed URLs (for backward compatibility)."""
    return [f.url for f in get_enabled_feeds()]


def get_quality_modifier(tier: QualityTier) -> float:
    """Get ranking weight modifier for a quality tier."""
    return QUALITY_TIER_MODIFIERS.get(tier, 1.0)


def get_decay_half_life(profile: DecayProfile) -> int:
    """Get decay half-life in hours for a profile."""
    return DECAY_HALF_LIFE_HOURS.get(profile, 24)


def get_role_quota(role: FeedRole) -> int:
    """Get target articles per day for a role."""
    return ROLE_QUOTAS.get(role, 3)


def get_feed_by_url(url: str) -> Optional[FeedConfig]:
    """Get feed config by URL."""
    for feed in FEED_REGISTRY:
        if feed.url == url:
            return feed
    return None


def get_feed_stats() -> Dict:
    """
    Get statistics about the feed configuration.
    
    Returns:
        Dictionary with feed counts and capacity info
    """
    enabled = get_enabled_feeds()
    
    feeds_by_role = {}
    for role in FeedRole:
        role_feeds = [f for f in enabled if f.role == role]
        feeds_by_role[role.value] = len(role_feeds)
    
    total_daily_cap = sum(f.daily_cap for f in enabled)
    premium_count = len([f for f in enabled if f.quality_tier == QualityTier.PREMIUM])
    
    return {
        "total_feeds": len(enabled),
        "total_daily_cap": total_daily_cap,
        "feeds_by_role": feeds_by_role,
        "premium_feeds": premium_count,
        "role_quotas": {r.value: q for r, q in ROLE_QUOTAS.items()},
        "target_articles_per_day": sum(ROLE_QUOTAS.values()),
    }
