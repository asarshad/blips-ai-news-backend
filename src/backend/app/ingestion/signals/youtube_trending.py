"""YouTube Trending signal fetcher.

Calls the YouTube Data API v3 ``videos.list`` endpoint to retrieve the
most-popular videos in the **Science & Technology** category (ID 28).

Requires:  settings.YOUTUBE_API_KEY

These video URLs then flow into the signal orchestrator, which checks
whether the video already exists in ``content_items`` (by canonical_key /
YouTube video ID).  If missing, the orchestrator enqueues an ingestion task.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import requests

from app.core.youtube_quota import YouTubeQuotaBudget
from app.ingestion.signals import SignalItem
from app.models.signal import SignalSource

logger = logging.getLogger(__name__)

_YT_VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"
_TIMEOUT = 10  # seconds

# YouTube category IDs relevant for Blips
# 28 = Science & Technology
_TECH_CATEGORY_ID = "28"


def fetch_yt_trending(
    api_key: Optional[str],
    region_code: str = "US",
    max_results: int = 50,
) -> List[SignalItem]:
    """Fetch most-popular YouTube videos in Science & Technology.

    Args:
        api_key: YouTube Data API v3 key.  Returns empty list when None/blank.
        region_code: ISO 3166-1 alpha-2 country code (default: US).
        max_results: Max videos to request from the API (≤ 50 per page).

    Returns:
        List of SignalItem with YouTube watch URLs.
    """
    if not api_key:
        logger.info("[yt_trending_signal] YOUTUBE_API_KEY not configured – skipping YT trending")
        return []

    quota = YouTubeQuotaBudget()
    if quota.is_locked_out():
        logger.warning("[yt_trending_signal] YouTube API lockout active – skipping YT trending")
        return []
    if not quota.try_reserve(1):
        logger.info("[yt_trending_signal] YouTube API budget exhausted – skipping YT trending")
        return []

    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "videoCategoryId": _TECH_CATEGORY_ID,
        "regionCode": region_code,
        "maxResults": min(max_results, 50),
    }

    try:
        resp = requests.get(
            _YT_VIDEOS_API,
            params=params,
            headers={"x-goog-api-key": api_key},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("[yt_trending_signal] YouTube API request failed: %s", exc)
        return []
    except ValueError:
        logger.warning("[yt_trending_signal] YouTube API returned invalid JSON")
        return []

    if resp.status_code == 403:
        errors = data.get("error", {}).get("errors", [])
        reason = errors[0].get("reason") if errors else data.get("error", {}).get("status")
        if reason == "quotaExceeded":
            quota.lock_out_until_reset(reason="quotaExceeded")
            logger.warning(
                "[yt_trending_signal] YouTube quotaExceeded; lockout active until Pacific reset"
            )
            return []

    if resp.status_code >= 400:
        errors = data.get("error", {}).get("errors", [])
        reason = errors[0].get("reason") if errors else data.get("error", {}).get("status")
        logger.warning(
            "[yt_trending_signal] YouTube API failed: HTTP %s%s",
            resp.status_code,
            f" ({reason})" if reason else "",
        )
        return []

    items = data.get("items", [])
    results: List[SignalItem] = []

    for item in items:
        video_id = item.get("id")
        if not video_id:
            continue

        snippet = item.get("snippet", {})
        title = snippet.get("title", "")

        stats = item.get("statistics", {})
        view_count_str = stats.get("viewCount", "0")
        try:
            view_count = int(view_count_str)
        except (ValueError, TypeError):
            view_count = 0

        video_url = f"https://www.youtube.com/watch?v={video_id}"

        results.append(
            SignalItem(
                raw_url=video_url,
                signal_source=SignalSource.YT_TRENDING,
                raw_title=title[:500],
                signal_score=view_count,
            )
        )

    logger.info(
        "[yt_trending_signal] YT Trending fetched %d videos (category=%s, region=%s)",
        len(results),
        _TECH_CATEGORY_ID,
        region_code,
    )
    return results
