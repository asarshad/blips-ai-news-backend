"""Hacker News signal fetcher.

Uses the Algolia HN Search API (no auth required, generous rate limits)
to retrieve the front-page and best-story items.

API docs: https://hn.algolia.com/api

Two signal sources:
- HN_TOP  → /search?tags=front_page  (current hot stories)
- HN_BEST → /search?tags=story&numericFilters=points>200  (quality filter)
"""

from __future__ import annotations

import logging
from typing import List, Optional

import requests

from app.ingestion.signals import SignalItem
from app.models.signal import SignalSource

logger = logging.getLogger(__name__)

_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
_TIMEOUT = 10  # seconds

# Minimum HN score to accept a story (avoids noise from low-vote items)
_MIN_SCORE = 50


def _fetch_algolia(tags: str, page_size: int = 60, extra_filters: str = "") -> list[dict]:
    """Fetch items from Algolia HN API."""
    params: dict = {
        "tags": tags,
        "hitsPerPage": page_size,
    }
    if extra_filters:
        params["numericFilters"] = extra_filters

    try:
        resp = requests.get(
            f"{_ALGOLIA_BASE}/search",
            params=params,
            timeout=_TIMEOUT,
            headers={"User-Agent": "blips-signal-fetcher/1.0"},
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except requests.RequestException as exc:
        logger.warning("[hn_signal] Algolia request failed: %s", exc)
        return []


def _hit_to_signal_item(
    hit: dict,
    source: SignalSource,
) -> Optional[SignalItem]:
    """Convert a single Algolia hit to a SignalItem, or None if unusable."""
    url = hit.get("url") or ""
    if not url:
        # Story without external URL (e.g., Ask HN / Show HN text posts)
        return None

    score = hit.get("points") or 0
    if score < _MIN_SCORE:
        return None

    title = hit.get("title") or ""
    return SignalItem(
        raw_url=url,
        signal_source=source,
        raw_title=title[:500] if title else None,
        signal_score=int(score),
    )


def fetch_hn_top(limit: int = 50) -> List[SignalItem]:
    """Fetch current HN front-page stories."""
    hits = _fetch_algolia(tags="front_page", page_size=min(limit, 100))
    results: List[SignalItem] = []
    for hit in hits:
        item = _hit_to_signal_item(hit, SignalSource.HN_TOP)
        if item:
            results.append(item)
    logger.info("[hn_signal] HN_TOP fetched %d usable URLs (of %d hits)", len(results), len(hits))
    return results[:limit]


def fetch_hn_best(limit: int = 50) -> List[SignalItem]:
    """Fetch high-quality HN stories filtered by minimum score."""
    hits = _fetch_algolia(
        tags="story",
        page_size=min(limit * 2, 100),
        extra_filters=f"points>{_MIN_SCORE * 2}",
    )
    results: List[SignalItem] = []
    for hit in hits:
        item = _hit_to_signal_item(hit, SignalSource.HN_BEST)
        if item:
            results.append(item)
    logger.info("[hn_signal] HN_BEST fetched %d usable URLs (of %d hits)", len(results), len(hits))
    return results[:limit]
