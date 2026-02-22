"""
Feed ad mixer — injects ad slots into organic feed lists.

Hard rules:
  - If ADS_ENABLED is ``False`` → inject NOTHING.
  - Never inject as the first item.
  - Never place two ads back-to-back.
  - Frequency: 1 ad every *N* organic items (``ADS_FEED_FREQUENCY``).
  - Ads count as a distinct "source" for diversity; they do not displace
    organic source-diversity slots.

Because no ad SDK is integrated, the mixer generates **placeholder** ad
items (sponsor_name = "Blips Sponsor") when ads are explicitly enabled.
A real provider adapter would replace ``_make_placeholder_ad`` with an
SDK call or sponsorship-backend fetch.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.ads import AdItem, AdTracking

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Placeholder ad factory (replace with real AdProvider later)
# ---------------------------------------------------------------------------

_PLACEHOLDER_SPONSOR = "Blips Sponsor"
_PLACEHOLDER_CLICK = "https://blips.dev"


def _make_placeholder_ad(placement_id: str, index: int) -> Dict[str, Any]:
    """Create a deterministic placeholder ad dict.

    The ``ad_id`` is derived from the placement + index so that the same
    request always produces the same placeholder set (useful for caching
    and debugging).
    """
    seed = f"{placement_id}:{index}"
    ad_id = hashlib.md5(seed.encode()).hexdigest()[:12]  # noqa: S324

    return AdItem(
        ad_id=ad_id,
        placement_id=placement_id,
        title="Discover what's new on Blips",
        body="Stay informed with curated tech news.",
        image_url=None,
        click_url=_PLACEHOLDER_CLICK,
        sponsor_name=_PLACEHOLDER_SPONSOR,
        label="Sponsored",
        tracking=AdTracking(impression_url=None, click_url=None),
        metadata=None,
    ).model_dump()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inject_ads(
    items: List[Dict[str, Any]],
    *,
    placement_id: str = "feed_fullpage",
    ads_enabled: Optional[bool] = None,
    frequency: Optional[int] = None,
    canary_percent: Optional[int] = None,
    request_fingerprint: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """Return a new list with ad slots injected according to rules.

    Parameters
    ----------
    items:
        Organic feed items (dicts with at least an ``id`` key).
    placement_id:
        Surface identifier attached to each ad.
    ads_enabled:
        Override ``settings.ADS_ENABLED`` (useful for tests).
    frequency:
        Override ``settings.ADS_FEED_FREQUENCY``.
    canary_percent:
        Override ``settings.ADS_CANARY_PERCENT``.
    request_fingerprint:
        Optional string used for deterministic canary bucketing.

    Returns
    -------
    (mixed_items, ads_injected_count)
    """
    enabled = ads_enabled if ads_enabled is not None else settings.ADS_ENABLED
    freq = frequency if frequency is not None else settings.ADS_FEED_FREQUENCY
    canary = canary_percent if canary_percent is not None else settings.ADS_CANARY_PERCENT

    # Gate 1: global kill switch
    if not enabled:
        return items, 0

    # Gate 2: frequency of 0 means disabled
    if freq <= 0:
        return items, 0

    # Gate 3: canary rollout — only serve ads to a percentage of requests
    if 0 < canary < 100:
        bucket = _canary_bucket(request_fingerprint)
        if bucket >= canary:
            return items, 0

    # --- Injection logic ---
    result: List[Dict[str, Any]] = []
    organic_since_last_ad = 0
    ad_counter = 0

    for idx, item in enumerate(items):
        result.append(item)
        organic_since_last_ad += 1

        # Rule: never inject as first item (idx == 0 means we just added first)
        if idx == 0:
            continue

        # Rule: inject after every N organic items
        if organic_since_last_ad >= freq:
            ad = _make_placeholder_ad(placement_id, ad_counter)
            result.append(ad)
            ad_counter += 1
            organic_since_last_ad = 0

    logger.debug(
        "Feed mixer: injected %d ads into %d organic items (freq=%d)",
        ad_counter,
        len(items),
        freq,
    )
    return result, ad_counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canary_bucket(fingerprint: Optional[str]) -> int:
    """Map a request fingerprint to a 0-99 bucket for canary rollout."""
    if not fingerprint:
        return random.randint(0, 99)  # noqa: S311
    digest = hashlib.md5(fingerprint.encode()).hexdigest()  # noqa: S324
    return int(digest[:8], 16) % 100
