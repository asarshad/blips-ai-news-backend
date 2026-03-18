"""Static YouTube discovery query packs for videos and reels."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config.search_query_registry import get_search_query_registry, iter_runtime_registry_rows

DISCOVERY_REGIONS = ("US", "CA", "GB", "IN")


@dataclass(frozen=True)
class DiscoveryQueryPack:
    """One search query tuned for a surface and editorial category."""

    label: str
    query: str
    category: str
    surface: str
    max_results: int = 25
    order: str = "relevance"
    priority: int = 3


def get_query_packs(surface: str) -> list[DiscoveryQueryPack]:
    """Return the configured query packs for a given surface."""

    packs: list[DiscoveryQueryPack] = []
    for row in iter_runtime_registry_rows(surface, mode="always_on"):
        query = row.query_for(surface)
        if not query:
            continue
        packs.append(
            DiscoveryQueryPack(
                label=row.id,
                query=query,
                category=row.category,
                surface=surface,
                max_results=25,
                order=row.order,
                priority=row.priority,
            )
        )
    return packs


# Validate the YAML-backed runtime registry during module import so bad config
# fails fast on boot instead of during the first live discovery window.
get_search_query_registry()


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
