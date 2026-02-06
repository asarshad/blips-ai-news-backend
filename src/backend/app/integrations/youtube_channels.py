"""
YouTube Channel Configuration for Blips Tech News.

This module defines the structured channel configuration with role-based
metadata for intelligent content ingestion. Each channel is tagged with:
- Role (content type/style)
- Content format (long-form vs shorts)
- Daily ingestion caps
- Quality tier

This structure enables:
- Balanced content mix across roles
- Prevention of creator fatigue
- Reliable shorts/reels volume
- Quality-first ingestion
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ChannelRole(str, Enum):
    """
    Content role classification for channels.
    
    Roles determine:
    - Ranking weight adjustments
    - Daily quota distribution
    - Content placement (Videos vs Reels tab)
    """
    EXPLAINER = "explainer"        # Reviews, tutorials, explainers (MKBHD, Dave2D)
    NEWS = "news"                  # Daily tech news coverage (Bloomberg, Verge)
    ENGINEER = "engineer"          # Deep technical content (Two Minute Papers, 3Blue1Brown)
    OFFICIAL = "official"          # Primary source announcements (Apple, Google, OpenAI)
    SHORTS = "shorts"              # Shorts/Reels native channels


class ContentFormat(str, Enum):
    """Content format classification."""
    LONG_FORM = "long_form"        # Standard videos (typically > 3 min)
    SHORTS = "shorts"              # YouTube Shorts (< 60 sec typically)
    MIXED = "mixed"                # Channel produces both formats


class QualityTier(str, Enum):
    """Quality tier for ranking weight adjustment."""
    PREMIUM = "premium"            # Top-tier creators, weight boost
    STANDARD = "standard"          # Normal ranking weight
    SUPPLEMENTAL = "supplemental"  # Fill content, slight weight reduction


@dataclass
class ChannelConfig:
    """
    Configuration for a single YouTube channel.
    
    Attributes:
        channel_id: YouTube channel ID
        name: Human-readable channel name
        role: Content role classification
        content_format: Long-form, shorts, or mixed
        daily_cap: Maximum videos to ingest per day from this channel
        quality_tier: Quality classification for ranking
        enabled: Whether channel is active for ingestion
        notes: Optional notes about the channel
    """
    channel_id: str
    name: str
    role: ChannelRole
    content_format: ContentFormat = ContentFormat.LONG_FORM
    daily_cap: int = 2
    quality_tier: QualityTier = QualityTier.STANDARD
    enabled: bool = True
    notes: str = ""
    
    @property
    def feed_url(self) -> str:
        """Generate YouTube RSS feed URL from channel ID."""
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"


# =============================================================================
# CHANNEL REGISTRY
# =============================================================================
# Organized by role for clarity. Each section targets specific content needs.
# Total long-form capacity: ~50-60 videos/day
# Total shorts capacity: ~40-50 reels/day

CHANNEL_REGISTRY: List[ChannelConfig] = [
    # =========================================================================
    # EXPLAINER / REVIEW CHANNELS
    # High-quality tech reviews, explainers, and tutorials
    # Target: 8-10 videos/day
    # =========================================================================
    ChannelConfig(
        channel_id="UCBJycsmduvYEL83R_U4JriQ",
        name="Marques Brownlee (MKBHD)",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Top tech reviewer, flagship content",
    ),
    ChannelConfig(
        channel_id="UCdBK94H6oZT2Q7l0-b0xmMg",
        name="ShortCircuit",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=3,
        quality_tier=QualityTier.PREMIUM,
        notes="Quick reviews, unboxings (LTT family)",
    ),
    ChannelConfig(
        channel_id="UC0vBXGSyV14uvJ4hECDOl0Q",
        name="TechLinked",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Daily tech news digest (LTT family)",
    ),
    ChannelConfig(
        channel_id="UCXuqSBlHAE6Xw-yeJA0Tunw",
        name="Linus Tech Tips",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.MIXED,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Comprehensive tech reviews and builds",
    ),
    ChannelConfig(
        channel_id="UCVYamHliCI9rw1tHR1xbkfw",
        name="Dave2D",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Clean laptop/phone reviews",
    ),
    ChannelConfig(
        channel_id="UCsTcErHg8oDvUnTzoqsYeNw",
        name="Unbox Therapy",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.MIXED,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Gadget unboxings and reviews",
    ),
    ChannelConfig(
        channel_id="UCXGgrKt94gR6lmN4aN3mYTg",
        name="Austin Evans",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Gaming and tech reviews",
    ),
    ChannelConfig(
        channel_id="UCMiJRAwDNSNzuYeN2uWa0pA",
        name="Mrwhosetheboss",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.MIXED,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="High production tech reviews",
    ),
    ChannelConfig(
        channel_id="UCmOdED66QPe_Z2IR1F17COg",
        name="Hardware Canucks",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="PC hardware reviews",
    ),
    ChannelConfig(
        channel_id="UC9-y-6csu5WGm29I7JiwpnA",
        name="Computerphile",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Computer science explanations",
    ),
    
    # =========================================================================
    # NEWS / COMMENTARY CHANNELS
    # Daily tech news, market analysis, industry commentary
    # Target: 8-10 videos/day (high daily volume expected)
    # =========================================================================
    ChannelConfig(
        channel_id="UCrM7B7SL_g1edFOnmj-SDKg",
        name="Bloomberg Technology",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=4,
        quality_tier=QualityTier.PREMIUM,
        notes="Professional tech news coverage",
    ),
    ChannelConfig(
        channel_id="UCvJJ_dzjViJCoLf5uKUTwoA",
        name="CNBC",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=3,
        quality_tier=QualityTier.STANDARD,
        notes="Business/tech news intersection",
    ),
    ChannelConfig(
        channel_id="UCK7tptUDHh-RYDsdxO1-5QQ",
        name="The Wall Street Journal",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=3,
        quality_tier=QualityTier.PREMIUM,
        notes="In-depth tech business analysis",
    ),
    ChannelConfig(
        channel_id="UCddiUEpeqJcYeBxX1IVBKvQ",
        name="The Verge",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.MIXED,
        daily_cap=3,
        quality_tier=QualityTier.PREMIUM,
        notes="Tech news and reviews",
    ),
    ChannelConfig(
        channel_id="UC7YOGHUfC1Tb6E4pudI9STA",
        name="Mental Outlaw",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Linux and privacy tech news",
    ),
    ChannelConfig(
        channel_id="UC2Xd-TjJByJyK2w1zNwY0zQ",
        name="Fireship",
        role=ChannelRole.NEWS,
        content_format=ContentFormat.MIXED,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Developer news and quick explainers",
    ),
    
    # =========================================================================
    # ENGINEER / DEEP TECH CHANNELS
    # Technical depth, academic content, engineering explanations
    # Target: 3-5 videos/day
    # =========================================================================
    ChannelConfig(
        channel_id="UCbfYPyITQ-7l4upoX8nvctg",
        name="Two Minute Papers",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="AI/ML research paper summaries",
    ),
    ChannelConfig(
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        name="3Blue1Brown",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=1,
        quality_tier=QualityTier.PREMIUM,
        notes="Math/CS visualizations, infrequent but high value",
    ),
    ChannelConfig(
        channel_id="UCS0N5baNlQWJCUrhCEo8WlA",
        name="Ben Eater",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=1,
        quality_tier=QualityTier.PREMIUM,
        notes="Hardware engineering deep dives",
    ),
    ChannelConfig(
        channel_id="UC8butISFwT-Wl7EV0hUK0BQ",
        name="freeCodeCamp",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Programming tutorials and courses",
    ),
    ChannelConfig(
        channel_id="UCsBjURrPoezykLs9EqgamOA",
        name="Fireship (100 seconds)",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Quick tech explainers for developers",
        enabled=False,  # Same channel as news Fireship, avoid double-counting
    ),
    ChannelConfig(
        channel_id="UCW5YeuERMmlnqo4oq8vwUpg",
        name="The Net Ninja",
        role=ChannelRole.ENGINEER,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.STANDARD,
        notes="Web development tutorials",
    ),
    
    # =========================================================================
    # OFFICIAL / PRIMARY SOURCE CHANNELS
    # Company announcements, keynotes, developer content
    # Target: 3-5 videos/day (down-ranked unless corroborated)
    # =========================================================================
    ChannelConfig(
        channel_id="UCXZCJLdBC09xxGZ6gcdrc6A",
        name="OpenAI",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="Official AI announcements",
    ),
    ChannelConfig(
        channel_id="UC_x5XG1OV2P6uZZ5FSM9Ttw",
        name="Google Developers",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="Google tech announcements",
    ),
    ChannelConfig(
        channel_id="UCVHFbqXqoYvEWM1Ddxl0QDg",
        name="Android Developers",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.MIXED,  # Channel posts both shorts and long videos
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="Android platform updates",
    ),
    ChannelConfig(
        channel_id="UCsMica-v34Irf9KVTh6xx-g",
        name="Microsoft Developer",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="Microsoft tech announcements",
    ),
    ChannelConfig(
        channel_id="UCE_M8A5yxnLfW0KghEeajjw",
        name="Apple",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="Apple official announcements",
    ),
    ChannelConfig(
        channel_id="UCd6MoB9NC6uYN2grvUNT-Zg",
        name="Amazon Web Services",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="AWS tech content",
    ),
    ChannelConfig(
        channel_id="UCL-g3eGJi1omSDSz48AML-g",
        name="NVIDIA",
        role=ChannelRole.OFFICIAL,
        content_format=ContentFormat.LONG_FORM,
        daily_cap=2,
        quality_tier=QualityTier.SUPPLEMENTAL,
        notes="NVIDIA GPU and AI announcements",
    ),
    
    # =========================================================================
    # SHORTS / REELS NATIVE CHANNELS
    # Channels that primarily produce short-form content
    # CRITICAL for meeting reels quota
    # Target: 30+ reels/day
    # =========================================================================
    ChannelConfig(
        channel_id="UCBJycsmduvYEL83R_U4JriQ",
        name="MKBHD (Shorts)",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=4,
        quality_tier=QualityTier.PREMIUM,
        notes="MKBHD short clips - same channel but different ingestion",
        enabled=False,  # Handled via main MKBHD entry with MIXED format detection
    ),
    ChannelConfig(
        channel_id="UCMiJRAwDNSNzuYeN2uWa0pA",
        name="Mrwhosetheboss (Shorts)",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=4,
        quality_tier=QualityTier.PREMIUM,
        notes="Mrwhosetheboss short clips",
        enabled=False,  # Handled via main entry with MIXED format detection
    ),
    ChannelConfig(
        channel_id="UCsTcErHg8oDvUnTzoqsYeNw",
        name="Unbox Therapy (Shorts)",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=4,
        quality_tier=QualityTier.STANDARD,
        notes="Quick gadget highlights",
        enabled=False,  # Handled via main entry
    ),
    # Dedicated shorts channels
    ChannelConfig(
        channel_id="UCVHFbqXqoYvEWM1Ddxl0QDg",
        name="Tech Vision",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=5,
        quality_tier=QualityTier.STANDARD,
        notes="Tech news shorts",
        enabled=True,
    ),
    ChannelConfig(
        channel_id="UCR-DXc1voovS8nhAvccRZhg",
        name="Jeff Geerling",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.MIXED,
        daily_cap=3,
        quality_tier=QualityTier.STANDARD,
        notes="Raspberry Pi and hardware shorts",
    ),
    ChannelConfig(
        channel_id="UCFhXFikryT4aFcLkLw2LBLA",
        name="NileRed Shorts",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Science experiment shorts",
    ),
    ChannelConfig(
        channel_id="UCnmGIkw-KdI0W5siakKPKog",
        name="Technology Connections Shorts",
        role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        daily_cap=2,
        quality_tier=QualityTier.PREMIUM,
        notes="Tech history and explainer shorts",
    ),
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_enabled_channels() -> List[ChannelConfig]:
    """Get all enabled channels."""
    return [ch for ch in CHANNEL_REGISTRY if ch.enabled]


def get_channels_by_role(role: ChannelRole) -> List[ChannelConfig]:
    """Get enabled channels for a specific role."""
    return [ch for ch in get_enabled_channels() if ch.role == role]


def get_long_form_channels() -> List[ChannelConfig]:
    """Get channels that produce long-form content."""
    return [
        ch for ch in get_enabled_channels()
        if ch.content_format in (ContentFormat.LONG_FORM, ContentFormat.MIXED)
    ]


def get_shorts_channels() -> List[ChannelConfig]:
    """Get channels that produce shorts content."""
    return [
        ch for ch in get_enabled_channels()
        if ch.content_format in (ContentFormat.SHORTS, ContentFormat.MIXED)
    ]


def get_channel_feed_urls() -> List[str]:
    """Get all enabled channel feed URLs (backward compatible)."""
    return [ch.feed_url for ch in get_enabled_channels()]


def get_channel_by_id(channel_id: str) -> Optional[ChannelConfig]:
    """Look up channel configuration by ID."""
    for ch in CHANNEL_REGISTRY:
        if ch.channel_id == channel_id:
            return ch
    return None


def get_channel_by_name(name: str) -> Optional[ChannelConfig]:
    """Look up channel configuration by name (case-insensitive partial match)."""
    name_lower = name.lower()
    for ch in CHANNEL_REGISTRY:
        if name_lower in ch.name.lower():
            return ch
    return None


def get_role_quotas() -> Dict[ChannelRole, Dict[str, int]]:
    """
    Calculate daily quotas by role.
    
    Returns:
        Dict mapping role to quota info (min, target, max)
    """
    return {
        ChannelRole.EXPLAINER: {"min": 6, "target": 10, "max": 15},
        ChannelRole.NEWS: {"min": 6, "target": 10, "max": 15},
        ChannelRole.ENGINEER: {"min": 2, "target": 5, "max": 8},
        ChannelRole.OFFICIAL: {"min": 2, "target": 4, "max": 6},
        ChannelRole.SHORTS: {"min": 20, "target": 30, "max": 50},
    }


def get_quality_weight_modifier(tier: QualityTier) -> float:
    """
    Get ranking weight modifier for quality tier.
    
    Returns:
        Multiplier for quality score (1.0 = no change)
    """
    return {
        QualityTier.PREMIUM: 1.15,
        QualityTier.STANDARD: 1.0,
        QualityTier.SUPPLEMENTAL: 0.85,
    }[tier]


# =============================================================================
# STATISTICS
# =============================================================================

def get_channel_stats() -> Dict[str, any]:
    """Get summary statistics about channel configuration."""
    enabled = get_enabled_channels()
    
    role_counts = {}
    for role in ChannelRole:
        role_counts[role.value] = len(get_channels_by_role(role))
    
    total_daily_cap = sum(ch.daily_cap for ch in enabled)
    long_form_cap = sum(ch.daily_cap for ch in get_long_form_channels())
    shorts_cap = sum(ch.daily_cap for ch in get_shorts_channels())
    
    return {
        "total_channels": len(enabled),
        "total_daily_cap": total_daily_cap,
        "long_form_daily_cap": long_form_cap,
        "shorts_daily_cap": shorts_cap,
        "channels_by_role": role_counts,
        "premium_channels": len([ch for ch in enabled if ch.quality_tier == QualityTier.PREMIUM]),
    }
