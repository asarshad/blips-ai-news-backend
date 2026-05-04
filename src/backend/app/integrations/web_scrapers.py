"""Web scrapers for sources that have no public RSS/Atom feed.

Each scraper is a callable with the signature::

    def scraper(max_entries: int) -> list[ScrapedEntry]

Scrapers are looked up by key in ``SCRAPER_REGISTRY`` and called from
``RSSClient.fetch_all_feeds`` when a ``FeedConfig`` has a ``scraper_key``
set instead of a parseable RSS URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Shared data class ─────────────────────────────────────────────────────────


@dataclass
class ScrapedEntry:
    """Minimal article descriptor returned by a web scraper."""

    url: str
    title: str
    description: str = ""
    image_url: Optional[str] = None
    published_date: Optional[datetime] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, *, timeout: float = 15.0) -> httpx.Response:
    return httpx.get(url, headers=_DEFAULT_HEADERS, timeout=timeout, follow_redirects=True)


# ── Anthropic news scraper ────────────────────────────────────────────────────

_ANTHROPIC_BASE = "https://www.anthropic.com"
_ANTHROPIC_NEWS_URL = "https://www.anthropic.com/news"

# Matches /news/<slug> paths (excludes the listing page itself and category pages)
_ANTHROPIC_POST_RE = re.compile(r"^/news/[a-z0-9][a-z0-9\-]+$")


def _anthropic_og_meta(soup: BeautifulSoup) -> dict:
    """Extract Open Graph metadata from a parsed page."""
    meta: dict = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name") or ""
        content = tag.get("content") or ""
        if prop in ("og:description", "description") and "description" not in meta:
            meta["description"] = content
        elif prop == "og:image" and "image" not in meta:
            meta["image"] = content
        elif prop in ("article:published_time", "og:updated_time") and "published" not in meta:
            meta["published"] = content
    return meta


def _parse_anthropic_published(value: str) -> Optional[datetime]:
    """Best-effort parse of an ISO-8601 or date string into a UTC datetime."""
    if not value:
        return None
    try:
        from dateutil import parser as du

        dt = du.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def scrape_anthropic_news(max_entries: int = 10) -> list[ScrapedEntry]:
    """Scrape the Anthropic /news listing page and return article descriptors.

    Fetches the listing once, extracts ``/news/<slug>`` hrefs, then fetches
    each article page individually to get Open Graph description + image.
    Individual article fetches are capped to ``max_entries``; failures are
    logged and skipped rather than propagated.
    """
    try:
        resp = _get(_ANTHROPIC_NEWS_URL)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[anthropic_scraper] failed to fetch news listing: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen_paths: set[str] = set()
    slugs: list[str] = []

    for a in soup.find_all("a", href=True):
        path = a["href"].rstrip("/")
        if _ANTHROPIC_POST_RE.match(path) and path not in seen_paths:
            seen_paths.add(path)
            slugs.append(path)
        if len(slugs) >= max_entries:
            break

    if not slugs:
        logger.info("[anthropic_scraper] no article links found on %s", _ANTHROPIC_NEWS_URL)
        return []

    results: list[ScrapedEntry] = []
    for path in slugs:
        url = urljoin(_ANTHROPIC_BASE, path)
        try:
            article_resp = _get(url)
            article_resp.raise_for_status()
            article_soup = BeautifulSoup(article_resp.text, "html.parser")

            # Title: prefer <h1>, fall back to <title>
            h1 = article_soup.find("h1")
            title_tag = article_soup.find("title")
            title = ""
            if h1:
                title = h1.get_text(strip=True)
            elif title_tag:
                title = title_tag.get_text(strip=True).split("|")[0].strip()

            if not title:
                logger.debug("[anthropic_scraper] skipping %s — no title found", url)
                continue

            og = _anthropic_og_meta(article_soup)
            results.append(
                ScrapedEntry(
                    url=url,
                    title=title,
                    description=og.get("description", ""),
                    image_url=og.get("image") or None,
                    published_date=_parse_anthropic_published(og.get("published", "")),
                )
            )
            logger.debug("[anthropic_scraper] scraped %s — %s", url, title)
        except Exception as exc:
            logger.warning("[anthropic_scraper] failed to fetch %s: %s", url, exc)
            continue

    logger.info("[anthropic_scraper] scraped %d articles from Anthropic news", len(results))
    return results


# ── Registry ───────────────────────────────────────────────────────────────────

SCRAPER_REGISTRY: dict[str, Callable[[int], list[ScrapedEntry]]] = {
    "anthropic_news": scrape_anthropic_news,
}


def get_scraper(key: str) -> Optional[Callable[[int], list[ScrapedEntry]]]:
    """Return the scraper callable for *key*, or None if not registered."""
    return SCRAPER_REGISTRY.get(key)
