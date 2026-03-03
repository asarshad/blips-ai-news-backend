"""GitHub Trending signal fetcher.

Scrapes https://github.com/trending (no auth required) to discover
repositories that are trending today, filtered to technology-relevant
topics (languages / tags that indicate software or systems work).

We only emit repository URLs, not individual file/commit links.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from app.ingestion.signals import SignalItem
from app.models.signal import SignalSource

logger = logging.getLogger(__name__)

_TRENDING_URL = "https://github.com/trending"
_TIMEOUT = 15  # seconds – GitHub can be slow

# GitHub trending page filter: most-popular daily repos (any language)
_DEFAULT_PARAMS = {
    "since": "daily",
    "spoken_language_code": "en",
}

# Minimum star count (today's stars) to accept as signal
_MIN_TODAY_STARS = 10

_GITHUB_BASE = "https://github.com"


def _parse_star_count(text: Optional[str]) -> int:
    """Parse '1,234 stars today' or '1.2k' style strings → int."""
    if not text:
        return 0
    text = text.strip().lower().replace(",", "")
    # Match patterns like "1234 stars today" or "1.2k stars today"
    m = re.search(r"([\d.]+)k?", text)
    if not m:
        return 0
    val = float(m.group(1))
    if "k" in text[m.start():m.end() + 1]:
        val *= 1000
    return int(val)


def fetch_github_trending(limit: int = 30) -> List[SignalItem]:
    """Fetch today's GitHub trending repositories and return as SignalItems."""
    try:
        resp = requests.get(
            _TRENDING_URL,
            params=_DEFAULT_PARAMS,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": "blips-signal-fetcher/1.0",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("[github_signal] GitHub trending request failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    repo_articles = soup.select("article.Box-row")

    results: List[SignalItem] = []
    for article in repo_articles[:limit * 2]:
        # Repository path e.g. "/owner/repo"
        h2 = article.find("h2")
        if not h2:
            continue
        link = h2.find("a", href=True)
        if not link:
            continue

        repo_path = link["href"].strip()
        if not repo_path.startswith("/"):
            continue
        repo_url = f"{_GITHUB_BASE}{repo_path}"

        # Description (optional)
        desc_el = article.find("p")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # Today's stars – look for the last <span> with "stars today"
        today_stars = 0
        for span in article.find_all("span"):
            text = span.get_text(strip=True)
            if "stars today" in text.lower() or "star today" in text.lower():
                today_stars = _parse_star_count(text)
                break

        if today_stars < _MIN_TODAY_STARS:
            continue

        # Build a descriptive title: "owner/repo – description"
        title = repo_path.lstrip("/")
        if description:
            title = f"{title} – {description[:200]}"

        results.append(
            SignalItem(
                raw_url=repo_url,
                signal_source=SignalSource.GITHUB_TRENDING,
                raw_title=title[:500],
                signal_score=today_stars,
            )
        )

        if len(results) >= limit:
            break

    logger.info(
        "[github_signal] GitHub Trending fetched %d repos (of %d articles)",
        len(results),
        len(repo_articles),
    )
    return results
