"""Helpers for compact discovery provenance labels."""

from __future__ import annotations

TRENDING_QUERY_LABEL = "most-popular-tech"
SEARCH_DISCOVERED_VIA_PREFIX = "yt_search:"
_YT_PREFIX = "yt_"


def build_discovered_via(acquisition_lane: str | None, query_label: str | None = None) -> str:
    """Build a compact discovered_via value without changing the DB schema."""

    lane = (acquisition_lane or "curated").strip().lower() or "curated"
    if lane == "search":
        normalized = str(query_label or "").strip()
        return (
            f"{SEARCH_DISCOVERED_VIA_PREFIX}{normalized}"
            if normalized
            else SEARCH_DISCOVERED_VIA_PREFIX.rstrip(":")
        )
    if lane == "trending":
        return "yt_trending"
    return f"yt_{lane}"


def query_label_from_discovered_via(
    discovered_via: str | None,
    *,
    acquisition_lane: str | None = None,
) -> str | None:
    """Extract the compact query label used for query-level metrics."""

    lane = (acquisition_lane or "").strip().lower()
    value = (discovered_via or "").strip()
    if value.startswith(SEARCH_DISCOVERED_VIA_PREFIX):
        suffix = value.split(":", 1)[1].strip()
        return suffix or "search"
    if lane == "search" or value == SEARCH_DISCOVERED_VIA_PREFIX.rstrip(":"):
        return "search"
    if lane == "trending" or value == "yt_trending":
        return TRENDING_QUERY_LABEL
    return None


def lane_from_discovered_via(discovered_via: str | None) -> str | None:
    """Recover the logical acquisition lane from compact discovery provenance."""

    value = (discovered_via or "").strip().lower()
    if not value.startswith(_YT_PREFIX):
        return None
    lane = value.removeprefix(_YT_PREFIX).split(":", 1)[0].strip()
    return lane if lane in {"curated", "search", "trending"} else None
