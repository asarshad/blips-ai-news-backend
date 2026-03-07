"""Discovery leads signal fetcher (Substack/Beehiiv-heavy).

This fetcher pulls recent posts from a curated set of newsletter/blog feeds that
surface high-signal emerging topics before they hit mainstream publishers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

import feedparser
import requests

from app.ingestion.signals import SignalItem
from app.ingestion.url_normalizer import normalize_url
from app.models.signal import SignalSource

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 12

# Curated discovery feeds from observed TLDR long-tail sources.
_DISCOVERY_FEED_SOURCES: Sequence[Tuple[str, str]] = (
    ("Speedrun", "https://speedrun.substack.com/feed"),
    ("Clouded Judgement", "https://cloudedjudgement.substack.com/feed"),
    ("Joe Reis", "https://joereis.substack.com/feed"),
    ("Latent Space", "https://latent.space/feed"),
    ("Product Picnic", "https://productpicnic.beehiiv.com/feed"),
    ("Cut Le Fish", "https://cutlefish.substack.com/feed"),
)


def _compute_recency_score(entry: dict) -> int:
    """Map entry recency to a lightweight integer popularity proxy."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return 25

    published_at = datetime(*published[:6], tzinfo=timezone.utc)
    age_hours = max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600.0)

    # 0h -> 100, 24h -> 76, 72h -> 28, floor at 5.
    return max(5, int(100 - min(age_hours, 95)))


def _entry_to_signal_item(raw_entry: dict) -> Optional[SignalItem]:
    raw_url = raw_entry.get("link") or ""
    canonical = normalize_url(raw_url)
    if not canonical:
        return None

    title = (raw_entry.get("title") or "").strip()

    return SignalItem(
        raw_url=canonical,
        signal_source=SignalSource.DISCOVERY_LEADS,
        raw_title=title[:500] if title else None,
        signal_score=_compute_recency_score(raw_entry),
    )


def fetch_discovery_leads(limit: int = 25, per_source_limit: int = 5) -> List[SignalItem]:
    """Fetch discovery leads from curated feed sources."""
    results: List[SignalItem] = []
    seen_urls: set[str] = set()

    max_total = max(1, int(limit))
    max_per_source = max(1, int(per_source_limit))

    for source_name, feed_url in _DISCOVERY_FEED_SOURCES:
        if len(results) >= max_total:
            break

        try:
            response = requests.get(
                feed_url,
                timeout=_TIMEOUT_SECONDS,
                headers={"User-Agent": "blips-discovery-signal/1.0"},
            )
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except requests.RequestException as exc:
            logger.warning("[discovery_signal] request failed for %s: %s", source_name, exc)
            continue
        except Exception as exc:
            logger.warning("[discovery_signal] parse failed for %s: %s", source_name, exc)
            continue

        added = 0
        for entry in parsed.entries:
            if added >= max_per_source or len(results) >= max_total:
                break

            item = _entry_to_signal_item(entry)
            if item is None or item.raw_url in seen_urls:
                continue

            seen_urls.add(item.raw_url)
            results.append(item)
            added += 1

        logger.info("[discovery_signal] %s produced %d leads", source_name, added)

    logger.info("[discovery_signal] total leads=%d", len(results))
    return results[:max_total]
