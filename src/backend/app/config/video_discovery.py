"""Static YouTube discovery query packs for videos and reels."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List

DISCOVERY_REGIONS = ("US", "CA", "GB", "IN")


@dataclass(frozen=True)
class DiscoveryQueryPack:
    """One search query tuned for a surface and editorial category."""

    label: str
    query: str
    category: str
    surface: str
    max_results: int = 6


VIDEO_QUERY_PACKS: List[DiscoveryQueryPack] = [
    DiscoveryQueryPack("news-tech", "technology news today", "news", "videos", 6),
    DiscoveryQueryPack("news-ai", "AI news today", "ai", "videos", 6),
    DiscoveryQueryPack(
        "explainer-mobile", "smartphone review hands on", "mobile/hardware", "videos", 6
    ),
    DiscoveryQueryPack(
        "explainer-hardware", "laptop review benchmark", "mobile/hardware", "videos", 6
    ),
    DiscoveryQueryPack("engineer-dev", "developer tooling update", "engineer/dev", "videos", 6),
    DiscoveryQueryPack(
        "security-privacy", "cybersecurity privacy update", "security/privacy", "videos", 6
    ),
    DiscoveryQueryPack(
        "industry-business", "tech industry analysis", "business/industry", "videos", 6
    ),
]

REEL_QUERY_PACKS: List[DiscoveryQueryPack] = [
    DiscoveryQueryPack("reels-tech-news", "technology news shorts", "news", "reels", 6),
    DiscoveryQueryPack("reels-ai", "AI update shorts", "ai", "reels", 6),
    DiscoveryQueryPack("reels-gadgets", "smartphone short review", "mobile/hardware", "reels", 6),
    DiscoveryQueryPack("reels-security", "privacy tips shorts", "security/privacy", "reels", 6),
    DiscoveryQueryPack("reels-dev", "developer tips shorts", "engineer/dev", "reels", 6),
]


def get_query_packs(surface: str) -> List[DiscoveryQueryPack]:
    """Return the configured query packs for a given surface."""
    if surface == "reels":
        return REEL_QUERY_PACKS
    return VIDEO_QUERY_PACKS


def discovery_cutoff(surface: str) -> datetime:
    """Search discovery stays aggressive on recency."""
    hours = 18 if surface == "reels" else 36
    return datetime.utcnow() - timedelta(hours=hours)


def iter_lane_matrix(surface: str) -> Iterable[tuple[DiscoveryQueryPack, str]]:
    """Yield every configured query pack x region combination."""
    for pack in get_query_packs(surface):
        for region in DISCOVERY_REGIONS:
            yield pack, region
