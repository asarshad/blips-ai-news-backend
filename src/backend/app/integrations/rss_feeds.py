"""
RSS Feed Configuration with Editorial Roles.

This module defines the complete feed registry with role-based metadata
for reliable, diverse tech news ingestion.

Target: ~50-55 high-quality, non-repetitive tech articles per day.

Architecture:
    - Each feed has an editorial ROLE (breaking, analysis, infra, etc.)
    - Quality tiers determine ranking weight modifiers
    - Per-feed daily caps prevent source dominance
    - Decay profiles control how quickly old articles lose ranking
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class FeedRole(Enum):
    """
    Editorial role defining the feed's purpose in the content mix.

    BREAKING: Fast-moving news, gadget launches, tech industry updates
    ANALYSIS: Deep dives, opinion, long-form context pieces
    AI: Artificial intelligence, machine learning, LLM developments
    INFRA: Cloud, DevOps, backend systems, enterprise tech
    SECURITY: Cybersecurity, privacy, threats, vulnerabilities
    BUSINESS: Startups, funding, acquisitions, market analysis
    DEV: Developer tools, tutorials, programming trends
    PRIMARY: Official company blogs (down-ranked unless corroborated)
    MAJOR_NEWS: Fast-path first-party feeds for major tech-company/news events
    """

    BREAKING = "breaking"
    ANALYSIS = "analysis"
    AI = "ai"
    INFRA = "infra"
    SECURITY = "security"
    BUSINESS = "business"
    DEV = "dev"
    PRIMARY = "primary"
    MAJOR_NEWS = "major_news"


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
    QualityTier.PREMIUM: 1.15,  # 15% boost
    QualityTier.STANDARD: 1.0,  # No modifier
    QualityTier.SUPPLEMENTAL: 0.85,  # 15% reduction
}

# Decay profile half-life in hours
DECAY_HALF_LIFE_HOURS: Dict[DecayProfile, int] = {
    DecayProfile.FAST: 6,
    DecayProfile.NORMAL: 24,
    DecayProfile.SLOW: 48,
}

# Role quotas - target articles per day per role
ROLE_QUOTAS: Dict[FeedRole, int] = {
    FeedRole.BREAKING: 12,  # Reduced: capped mid-tier consumer overlap
    FeedRole.ANALYSIS: 10,  # Deep dives
    FeedRole.AI: 12,  # AI/ML focused content
    FeedRole.INFRA: 8,  # Increased: cloud-native + DevOps coverage
    FeedRole.SECURITY: 5,  # Slight increase for security depth
    FeedRole.BUSINESS: 4,  # Startups/funding
    FeedRole.DEV: 5,  # Developer content + mobile
    FeedRole.PRIMARY: 2,  # Official blogs (down-ranked)
    FeedRole.MAJOR_NEWS: 12,  # Bounded fast-path probe; separate from normal RSS defaults
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
# Total daily capacity: ~100+ articles (with caps)
# Target after deduplication: ~50-55 articles/day

FEED_REGISTRY: List[FeedConfig] = [
    # =========================================================================
    # MAJOR NEWS FAST PATH
    # First-party feeds only; probed by the bounded major_news_probe task.
    # These are excluded from normal checkpointed RSS defaults to avoid double
    # counting and keep the fast path operationally isolated.
    # =========================================================================
    FeedConfig(
        url="https://www.cnbc.com/id/19854910/device/rss/rss.html",
        name="CNBC Technology",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=5,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.88,
        notes="Fast major-company, market, and policy coverage",
    ),
    FeedConfig(
        url="https://www.platformer.news/rss/",
        name="Platformer",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.90,
        notes="Social platforms, AI companies, governance, major tech power",
    ),
    FeedConfig(
        url="https://www.bigtechnology.com/feed",
        name="Big Technology",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.88,
        notes="Big Tech reporting and interviews",
    ),
    FeedConfig(
        url="https://www.fabricatedknowledge.com/feed",
        name="Fabricated Knowledge",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.90,
        notes="Semiconductors and AI infrastructure supply chain",
    ),
    FeedConfig(
        url="https://www.ben-evans.com/benedictevans?format=rss",
        name="Benedict Evans",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="Macro tech strategy and market structure",
    ),
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
        daily_cap=2,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Consumer tech news and reviews",
    ),
    FeedConfig(
        url="https://www.zdnet.com/news/rss.xml",
        name="ZDNet",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
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
    FeedConfig(
        url="https://www.theregister.com/headlines.atom",
        name="The Register",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.82,
        notes="Enterprise IT, cloud, security, chips, and sysadmin news",
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
    FeedConfig(
        url="https://stratechery.com/feed/",
        name="Stratechery",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.92,
        notes="Technology strategy, platforms, business models, and market structure",
    ),
    FeedConfig(
        url="https://www.404media.co/rss/",
        name="404 Media",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="Investigative technology journalism, platforms, surveillance, and online culture",
    ),
    FeedConfig(
        url="https://themarkup.org/feeds/rss.xml",
        name="The Markup",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.88,
        notes="Data-driven accountability journalism on platforms, privacy, and algorithms",
    ),
    FeedConfig(
        url="https://restofworld.org/feed/latest/",
        name="Rest of World",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.86,
        notes="Technology impact and platform reporting outside Silicon Valley",
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
        url="https://www.cncf.io/blog/feed/",
        name="CNCF Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.83,
        notes="Cloud Native Computing Foundation — Kubernetes ecosystem, CNCF projects",
    ),
    FeedConfig(
        url="https://kubernetes.io/feed.xml",
        name="Kubernetes Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.82,
        notes="Official K8s blog — release notes, deep dives, community",
    ),
    FeedConfig(
        url="https://blog.cloudflare.com/rss/",
        name="Cloudflare Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="Highly technical posts on network infra, security, edge computing",
    ),
    FeedConfig(
        url="https://siliconangle.com/feed/",
        name="SiliconANGLE",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="Enterprise cloud, data platforms, security, and infrastructure news",
    ),
    FeedConfig(
        url="https://www.infoworld.com/feed/",
        name="InfoWorld",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="Developer, cloud, enterprise software, and IT architecture coverage",
    ),
    FeedConfig(
        url="https://www.hashicorp.com/blog/feed.xml",
        name="HashiCorp Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.80,
        notes="Terraform, Vault, Nomad — infra-as-code and secrets management",
    ),
    FeedConfig(
        url="https://aws.amazon.com/blogs/aws/feed/",
        name="AWS Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        enabled=False,
        notes="Enterprise noise, low consumer engagement",
    ),
    FeedConfig(
        url="https://cloudblog.withgoogle.com/rss/",
        name="Google Cloud Blog",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        enabled=False,
        notes="Enterprise announcements, low consumer engagement",
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
    FeedConfig(
        url="https://strictlyvc.com/feed/",
        name="StrictlyVC",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.78,
        notes="Venture capital deals, startup funding rounds, investor moves",
    ),
    FeedConfig(
        url="https://techcrunch.com/category/startups/feed/",
        name="TechCrunch Startups",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.85,
        notes="Startup-specific lane from TechCrunch for funding and launch coverage",
    ),
    # =========================================================================
    # FINTECH
    # Payments, banking infrastructure, digital finance, compliance
    # Target: 2-3 articles/day
    # =========================================================================
    FeedConfig(
        url="https://www.finextra.com/rss/headlines.aspx",
        name="Finextra",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="Fintech infrastructure, banking rails, regulation, and payments tech",
    ),
    FeedConfig(
        url="https://www.bankingdive.com/feeds/news/",
        name="Banking Dive",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.76,
        notes="Banking operations, digital transformation, and policy-driven market updates",
    ),
    # =========================================================================
    # ROBOTICS
    # Robotics systems, automation engineering, industrial deployment
    # Target: 2-3 articles/day
    # =========================================================================
    FeedConfig(
        url="https://ramagazine.ieee.org/feed/",
        name="IEEE Robotics and Automation Magazine",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.90,
        notes="Peer-reviewed robotics and automation engineering perspectives",
    ),
    FeedConfig(
        url="https://www.therobotreport.com/feed/",
        name="The Robot Report",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.82,
        notes="Industry robotics product updates, deployments, and vendor ecosystem coverage",
    ),
    # =========================================================================
    # CLIMATE / GREEN TECH
    # Electrification, clean energy technology, climate-adjacent innovation
    # Target: 2-3 articles/day
    # =========================================================================
    FeedConfig(
        url="https://cleantechnica.com/feed/",
        name="CleanTechnica",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.76,
        notes="Clean energy, battery, and sustainability technology coverage",
    ),
    FeedConfig(
        url="https://electrek.co/feed/",
        name="Electrek",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.76,
        notes="EV, charging, and energy transition product updates",
    ),
    # =========================================================================
    # BIOTECH & HEALTH TECH
    # Biotech R&D, clinical innovation, health systems technology
    # Target: 2-3 articles/day
    # =========================================================================
    FeedConfig(
        url="https://www.statnews.com/feed/",
        name="STAT News",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.86,
        notes="Biotech and health tech reporting with strong editorial rigor",
    ),
    FeedConfig(
        url="https://www.technologyreview.com/topic/biomedicine/feed/",
        name="MIT Technology Review (Biomedicine)",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.84,
        notes="Biomedicine and health innovation analysis",
    ),
    # =========================================================================
    # REGULATORY / POLICY
    # Privacy, civil liberties, platform and technology policy
    # Target: 1-2 articles/day
    # =========================================================================
    FeedConfig(
        url="https://www.eff.org/rss/updates.xml",
        name="EFF Deep Links",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.85,
        notes="Technology policy, surveillance, and digital rights analysis",
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
    FeedConfig(
        url="https://www.bleepingcomputer.com/feed/",
        name="BleepingComputer",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.80,
        notes="Malware, ransomware, breaches, Windows, and security incident coverage",
    ),
    FeedConfig(
        url="https://www.techdirt.com/feed/",
        name="Techdirt",
        role=FeedRole.SECURITY,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="Internet policy, copyright, platforms, privacy, and technology law",
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
        url="https://github.blog/feed/",
        name="GitHub Blog",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.82,
        notes="Developer tooling, open source, software supply chain, and GitHub platform updates",
    ),
    FeedConfig(
        url="https://css-tricks.com/feed/",
        name="CSS-Tricks",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.78,
        enabled=False,
        notes="Nearly dead since DigitalOcean acquisition",
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
        base_quality_weight=0.70,
        enabled=False,
        notes="Covered by TechCrunch/Verge, feed often breaks",
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
        base_quality_weight=0.70,
        enabled=False,
        notes="Press releases, covered faster by 9to5Mac/MacRumors",
    ),
    # =========================================================================
    # MOBILE & CONSUMER TECH (NEW)
    # Apple, Android, mobile devices, consumer gadgets
    # Target: 8-10 articles/day
    # =========================================================================
    FeedConfig(
        url="https://9to5mac.com/feed/",
        name="9to5Mac",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.88,
        notes="Apple ecosystem news, leaks, reviews",
    ),
    FeedConfig(
        url="https://9to5google.com/feed/",
        name="9to5Google",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.88,
        notes="Google/Android ecosystem news, Pixel, Chrome",
    ),
    FeedConfig(
        url="https://www.androidauthority.com/feed/",
        name="Android Authority",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.78,
        notes="Android reviews, tutorials, buying guides",
    ),
    FeedConfig(
        url="https://feeds.macrumors.com/MacRumors-All",
        name="MacRumors",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.80,
        notes="Apple rumors, product launches, buying guides",
    ),
    FeedConfig(
        url="https://www.xda-developers.com/feed/",
        name="XDA Developers",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.75,
        notes="Mobile dev, Android mods, phone reviews",
    ),
    FeedConfig(
        url="https://www.digitaltrends.com/feed/",
        name="Digital Trends",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Consumer tech, lifestyle tech, buying guides",
    ),
    FeedConfig(
        url="https://www.techradar.com/rss",
        name="TechRadar",
        role=FeedRole.BREAKING,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.75,
        notes="Reviews, deals, consumer tech news",
    ),
    FeedConfig(
        url="https://www.tomshardware.com/feeds/all",
        name="Tom's Hardware",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.80,
        notes="PC hardware benchmarks, GPU reviews, components",
    ),
    # =========================================================================
    # AI & MACHINE LEARNING
    # Official lab blogs, research publications, AI infrastructure
    # Target: 10-12 articles/day (capped at 40 % of any window by DiversityMixer)
    # =========================================================================
    # ── Primary lab blogs ─────────────────────────────────────────────────────
    FeedConfig(
        url="https://openai.com/news/rss.xml",
        name="OpenAI Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="Model releases, safety research, product launches",
    ),
    FeedConfig(
        url="https://www.anthropic.com/rss.xml",
        name="Anthropic Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="Claude updates, alignment research, interpretability",
    ),
    FeedConfig(
        url="https://deepmind.google/blog/rss.xml",
        name="Google DeepMind Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.92,
        notes="Gemini, AlphaFold, fundamental AI research",
    ),
    FeedConfig(
        url="https://ai.meta.com/blog/rss/",
        name="Meta AI Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        enabled=False,
        base_quality_weight=0.85,
        notes="Disabled March 28, 2026: feed URL returns 404 from both local checks and Render",
    ),
    FeedConfig(
        url="https://blogs.microsoft.com/ai/feed/",
        name="Microsoft AI Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.85,
        notes="Copilot, Azure AI, responsible AI posts",
    ),
    FeedConfig(
        url="https://huggingface.co/blog/feed.xml",
        name="Hugging Face Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.88,
        notes="OSS model releases, datasets, inference",
    ),
    FeedConfig(
        url="https://simonwillison.net/atom/everything/",
        name="Simon Willison's Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.92,
        notes="LLM tools, prompt engineering, AI analysis from Django co-creator",
    ),
    # ── Research publications ─────────────────────────────────────────────────
    FeedConfig(
        url="https://paperswithcode.com/rss",
        name="Papers With Code",
        role=FeedRole.AI,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.SLOW,
        enabled=False,
        base_quality_weight=0.80,
        notes="Disabled March 28, 2026: URL redirects away from an RSS feed and parses as empty/broken",
    ),
    FeedConfig(
        url="https://export.arxiv.org/rss/cs.AI",
        name="arXiv cs.AI",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.90,
        notes="AI research preprints to restore academic-research signal coverage",
    ),
    FeedConfig(
        url="https://hai.stanford.edu/news/feed",
        name="Stanford HAI",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        enabled=False,
        base_quality_weight=0.90,
        notes="Disabled March 28, 2026: endpoint serves HTML instead of a valid feed",
    ),
    FeedConfig(
        url="https://news.csail.mit.edu/feed/",
        name="MIT CSAIL",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        enabled=False,
        base_quality_weight=0.88,
        notes="Disabled March 28, 2026: consistently unreachable from Render worker",
    ),
    # ── AI infrastructure & chips ─────────────────────────────────────────────
    FeedConfig(
        url="https://blogs.nvidia.com/blog/category/artificial-intelligence/feed/",
        name="NVIDIA AI Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        enabled=False,
        base_quality_weight=0.78,
        notes="Disabled March 28, 2026: feed URL returns 404 from both local checks and Render",
    ),
    FeedConfig(
        url="https://aws.amazon.com/blogs/machine-learning/feed/",
        name="AWS Machine Learning Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.75,
        notes="SageMaker, Bedrock, cloud ML tooling",
    ),
    FeedConfig(
        url="https://www.semianalysis.com/feed",
        name="SemiAnalysis",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.90,
        notes="Deep chip architecture analysis, TPU/GPU economics",
    ),
    FeedConfig(
        url="https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/bg-p/Azure-AI-Services-Blog/rss",
        name="Azure AI Blog",
        role=FeedRole.AI,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        enabled=False,
        base_quality_weight=0.78,
        notes="Disabled March 28, 2026: feed endpoint returns 403 and yields no entries",
    ),
    FeedConfig(
        url="https://www.deeplearning.ai/the-batch/feed/",
        name="The Batch (Andrew Ng)",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.90,
        enabled=False,
        notes="Weekly AI/ML newsletter — feed URL returns 404, needs investigation",
    ),
    # =========================================================================
    # DISCOVERY NEWSLETTERS (TLDR-DERIVED)
    # Substack/Beehiiv sources with low caps for controlled direct coverage
    # =========================================================================
    FeedConfig(
        url="https://speedrun.substack.com/feed",
        name="Speedrun",
        role=FeedRole.AI,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        base_quality_weight=0.78,
        notes="AI product + model launch analysis (Substack)",
    ),
    FeedConfig(
        url="https://cloudedjudgement.substack.com/feed",
        name="Clouded Judgement",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.86,
        notes="Cloud infrastructure and hyperscaler cost analysis (Substack)",
    ),
    FeedConfig(
        url="https://joereis.substack.com/feed",
        name="Joe Reis",
        role=FeedRole.INFRA,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.82,
        notes="Data engineering and platform architecture analysis (Substack)",
    ),
    FeedConfig(
        url="https://latent.space/feed",
        name="Latent Space",
        role=FeedRole.AI,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.88,
        notes="Applied AI engineering and agent systems analysis",
    ),
    FeedConfig(
        url="https://productpicnic.beehiiv.com/feed",
        name="Product Picnic",
        role=FeedRole.BUSINESS,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.NORMAL,
        enabled=False,
        base_quality_weight=0.76,
        notes="Disabled March 28, 2026: feed is blocked by Cloudflare challenge/403 from ingestion",
    ),
    FeedConfig(
        url="https://cutlefish.substack.com/feed",
        name="Cut Le Fish",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        base_quality_weight=0.78,
        notes="Engineering leadership and software strategy essays (Substack)",
    ),
    # =========================================================================
    # PRODUCT & STARTUP DISCOVERY (NEW)
    # Product launches, indie tools, startup ecosystem
    # =========================================================================
    FeedConfig(
        url="https://www.producthunt.com/feed",
        name="Product Hunt",
        role=FeedRole.DEV,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
        decay_profile=DecayProfile.FAST,
        base_quality_weight=0.70,
        notes="New product launches, indie tools, startup ecosystem",
    ),
    # =========================================================================
    # PREMIUM ANALYSIS (NEW)
    # Investigative tech journalism, premium reporting
    # =========================================================================
    FeedConfig(
        url="https://www.theinformation.com/feed",
        name="The Information",
        role=FeedRole.ANALYSIS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=1,
        decay_profile=DecayProfile.SLOW,
        enabled=False,
        base_quality_weight=0.95,
        notes="Disabled March 28, 2026: feed returns 403 from Render despite public availability elsewhere",
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
