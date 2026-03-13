"""Static YouTube discovery query packs for videos and reels."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

DISCOVERY_REGIONS = ("US", "CA", "GB", "IN")


@dataclass(frozen=True)
class DiscoveryQueryPack:
    """One search query tuned for a surface and editorial category."""

    label: str
    query: str
    category: str
    surface: str
    max_results: int = 25


VIDEO_QUERY_PACKS: List[DiscoveryQueryPack] = [
    DiscoveryQueryPack("news-tech", "technology news", "news", "videos", 25),
    DiscoveryQueryPack("news-ai", "AI update", "ai", "videos", 25),
    DiscoveryQueryPack(
        "explainer-mobile", "smartphone hands on review", "mobile/hardware", "videos", 25
    ),
    DiscoveryQueryPack(
        "explainer-hardware", "laptop review benchmark", "mobile/hardware", "videos", 25
    ),
    DiscoveryQueryPack("engineer-dev", "developer tooling release", "engineer/dev", "videos", 25),
    DiscoveryQueryPack(
        "security-privacy", "cybersecurity privacy update", "security/privacy", "videos", 25
    ),
    DiscoveryQueryPack(
        "industry-business", "tech industry analysis", "business/industry", "videos", 25
    ),
]

REEL_QUERY_PACKS: List[DiscoveryQueryPack] = [
    DiscoveryQueryPack("reels-tech-news", "technology shorts", "news", "reels", 25),
    DiscoveryQueryPack("reels-ai", "AI update shorts", "ai", "reels", 25),
    DiscoveryQueryPack(
        "reels-gadgets",
        "smartphone hands on shorts",
        "mobile/hardware",
        "reels",
        25,
    ),
    DiscoveryQueryPack("reels-security", "privacy update shorts", "security/privacy", "reels", 25),
    DiscoveryQueryPack("reels-dev", "developer tips shorts", "engineer/dev", "reels", 25),
]


def get_query_packs(surface: str) -> List[DiscoveryQueryPack]:
    """Return the configured query packs for a given surface."""
    if surface == "reels":
        return REEL_QUERY_PACKS
    return VIDEO_QUERY_PACKS


def discovery_cutoff(surface: str) -> datetime:
    """Search discovery uses a weekly pool and lets ranking decide what stays hot."""
    env_name = (
        "YOUTUBE_DISCOVERY_REELS_LOOKBACK_DAYS"
        if surface == "reels"
        else "YOUTUBE_DISCOVERY_VIDEOS_LOOKBACK_DAYS"
    )
    lookback_days = max(
        1, int(os.getenv(env_name, os.getenv("YOUTUBE_DISCOVERY_LOOKBACK_DAYS", "7")))
    )
    return datetime.utcnow() - timedelta(days=lookback_days)
