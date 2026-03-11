"""
RSS feed client.

Handles fetching and parsing RSS feeds, with support for
content extraction from linked articles.

Enhanced with role-based feed configuration for diverse,
high-quality content ingestion.
"""

import html
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.extraction.normalize import make_absolute_url, validate_image_url
from app.integrations.rss_feeds import (
    DecayProfile,
    FeedConfig,
    FeedRole,
    QualityTier,
    get_enabled_feeds,
)

logger = get_logger(__name__)


@dataclass
class FeedEntry:
    """
    Represents a parsed RSS feed entry with role metadata.

    Enhanced to include feed configuration metadata for
    role-based ranking and decay.
    """

    title: str
    url: str
    content: str
    image_url: str
    published_date: datetime
    # Role-based metadata
    feed_name: str = ""
    feed_role: Optional[FeedRole] = None
    quality_tier: Optional[QualityTier] = None
    decay_profile: Optional[DecayProfile] = None
    base_quality_weight: Optional[float] = None


def decode_html_entities(text: str) -> str:
    """Decode HTML entities in text."""
    if not text:
        return text
    # First pass: decode HTML entities like &#8217;
    decoded = html.unescape(text)
    # Second pass: use BeautifulSoup to handle any remaining entities
    soup = BeautifulSoup(decoded, "html.parser")
    return soup.get_text()


class RSSClient:
    """
    Client for fetching and parsing RSS feeds.

    Enhanced with role-based feed configuration for diverse,
    balanced content ingestion. Uses the new FeedConfig system
    to track metadata through the pipeline.
    """

    # User agents for rotation to avoid blocking
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
    ]

    def __init__(self, feed_configs: Optional[List[FeedConfig]] = None):
        """
        Initialize RSS client with feed configurations.

        Args:
            feed_configs: List of FeedConfig objects. Defaults to enabled feeds from registry.
        """
        self.feed_configs = feed_configs or get_enabled_feeds()
        # Build URL -> config lookup
        self._config_by_url = {f.url: f for f in self.feed_configs}
        # Shared retry budget for this client instance/run.
        self._retry_budget_remaining = max(0, int(os.getenv("CONNECTOR_RETRY_BUDGET", "40")))
        self._max_retries = max(0, int(os.getenv("CONNECTOR_MAX_RETRIES", "2")))
        self._timeout_seconds = max(1.0, float(os.getenv("CONNECTOR_TIMEOUT_SECONDS", "15")))
        self._backoff_base_seconds = max(
            0.0, float(os.getenv("CONNECTOR_BACKOFF_BASE_SECONDS", "0.5"))
        )
        self._image_fallback_enabled = os.getenv("RSS_IMAGE_FALLBACK_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            self._image_fallback_budget_remaining = max(
                0, int(os.getenv("RSS_IMAGE_FALLBACK_BUDGET", "25"))
            )
        except ValueError:
            self._image_fallback_budget_remaining = 25

    def fetch_all_feeds(self, entries_per_feed: int = 10) -> List[FeedEntry]:
        """
        Fetch entries from all configured feeds with role metadata.

        Args:
            entries_per_feed: Maximum entries to fetch per feed

        Returns:
            List of FeedEntry objects with role metadata
        """
        entries = []
        entries_by_role: Dict[str, int] = {}

        # Headroom multiplier: fetch more candidates than the daily_cap target
        # so the quality/dedup pipeline has enough to fill the budget even after
        # filtering.  The budget system (IngestionBudgetRepository) enforces the
        # hard per-feed cap downstream; this is a pre-dedup fetch buffer only.
        _FETCH_HEADROOM = 3

        for feed_config in self.feed_configs:
            try:
                max_entries = min(entries_per_feed, feed_config.daily_cap * _FETCH_HEADROOM)
                feed_entries = self.fetch_feed(feed_config.url, max_entries)

                # Attach role metadata to entries
                for entry in feed_entries:
                    entry.feed_name = feed_config.name
                    entry.feed_role = feed_config.role
                    entry.quality_tier = feed_config.quality_tier
                    entry.decay_profile = feed_config.decay_profile
                    entry.base_quality_weight = feed_config.base_quality_weight

                entries.extend(feed_entries)

                # Track by role
                role_key = feed_config.role.value
                entries_by_role[role_key] = entries_by_role.get(role_key, 0) + len(feed_entries)

            except Exception as e:
                logger.error(f"Error fetching feed {feed_config.name}: {str(e)}")

        logger.info(f"Total RSS entries fetched: {len(entries)}")
        for role, count in sorted(entries_by_role.items()):
            logger.info(f"  {role}: {count} entries")

        return entries

    def fetch_feed(self, feed_url: str, max_entries: int = 10) -> List[FeedEntry]:
        """
        Fetch entries from a single RSS feed.

        Args:
            feed_url: URL of the RSS feed
            max_entries: Maximum entries to return

        Returns:
            List of FeedEntry objects
        """
        entries = []

        try:
            logger.info(f"Fetching feed: {feed_url}")

            # Allow tests / explicit XML input to bypass network.
            if feed_url.lstrip().startswith("<"):
                feed = feedparser.parse(feed_url)
            else:
                content = self._fetch_feed_content_with_retries(feed_url)
                if content is None:
                    logger.warning(f"Feed fetch failed after retries: {feed_url}")
                    return []
                feed = feedparser.parse(content)

            if feed.bozo and feed.bozo_exception:
                logger.warning(f"Feed parse warning for {feed_url}: {feed.bozo_exception}")

            if not feed.entries:
                logger.warning(
                    f"Feed returned 0 entries: {feed_url} (status={getattr(feed, 'status', 'unknown')})"
                )
                return []

            logger.info(f"Feed {feed_url} returned {len(feed.entries)} entries")

            for entry in feed.entries[:max_entries]:
                try:
                    # Decode HTML entities in title
                    title = decode_html_entities(entry.title)
                    url = entry.link

                    # Use RSS feed content only (no full-page scraping)
                    content = self._get_rss_description(entry)
                    if not content:
                        logger.warning(f"Skipped article with no RSS content: {title}")
                        continue

                    # Get image
                    image_url = self._extract_image_url(entry, url)

                    # Parse date
                    published_date = self._parse_date(entry)

                    entries.append(
                        FeedEntry(
                            title=title,
                            url=url,
                            content=content,
                            image_url=image_url,
                            published_date=published_date,
                        )
                    )

                    logger.info(f"Added article: {title}")

                except Exception as e:
                    logger.error(f"Error processing entry: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing feed {feed_url}: {str(e)}")
            raise

        return entries

    def _fetch_feed_content_with_retries(self, feed_url: str) -> Optional[bytes]:
        """Fetch RSS bytes with retry/backoff bounded by a shared per-run budget."""
        headers = {"User-Agent": random.choice(self.USER_AGENTS)}
        attempts = self._max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(feed_url, headers=headers, timeout=self._timeout_seconds)
                response.raise_for_status()
                return response.content
            except requests.RequestException as exc:
                last_attempt = attempt >= attempts
                budget_exhausted = self._retry_budget_remaining <= 0
                if last_attempt or budget_exhausted:
                    logger.warning(
                        "RSS fetch failed (url=%s attempt=%s/%s budget_left=%s): %s",
                        feed_url,
                        attempt,
                        attempts,
                        self._retry_budget_remaining,
                        exc,
                    )
                    return None

                self._retry_budget_remaining -= 1
                delay = self._backoff_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "RSS fetch retry scheduled (url=%s next_attempt=%s/%s delay=%.2fs budget_left=%s)",
                    feed_url,
                    attempt + 1,
                    attempts,
                    delay,
                    self._retry_budget_remaining,
                )
                if delay > 0:
                    time.sleep(delay)

        return None

    # _extract_article_content removed — we now use RSS descriptions
    # only (no full-page scraping) to respect copyright and avoid SSRF.

    def _get_rss_description(self, entry) -> str:
        """Extract content from RSS feed's description/summary fields."""
        content = ""

        # Try content:encoded first (full content in some feeds)
        if hasattr(entry, "content") and entry.content:
            for c in entry.content:
                if c.get("value"):
                    content = c.get("value", "")
                    break

        # Fallback to summary/description
        if not content:
            content = getattr(entry, "summary", "") or getattr(entry, "description", "")

        if content:
            # Strip HTML tags and clean up
            soup = BeautifulSoup(content, "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            return text[:8000] if text else ""

        return ""

    def _extract_image_url(self, entry, article_url: str) -> str:
        """Extract featured image URL from RSS metadata, then page metadata fallback."""
        # Check media content
        if hasattr(entry, "media_content") and entry.media_content:
            for media in entry.media_content:
                candidate = self._validate_image_candidate(media.get("url"), article_url)
                if candidate:
                    return candidate

        # Check media_thumbnail
        if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            for thumb in entry.media_thumbnail:
                candidate = self._validate_image_candidate(thumb.get("url"), article_url)
                if candidate:
                    return candidate

        # Check enclosures
        if hasattr(entry, "enclosures") and entry.enclosures:
            for enclosure in entry.enclosures:
                if isinstance(enclosure, dict):
                    enclosure_url = enclosure.get("url")
                    enclosure_type = enclosure.get("type", "")
                else:
                    enclosure_url = getattr(enclosure, "url", None)
                    enclosure_type = getattr(enclosure, "type", "")
                if enclosure_type and str(enclosure_type).startswith("image"):
                    candidate = self._validate_image_candidate(enclosure_url, article_url)
                    if candidate:
                        return candidate

        # Check summary/content for images
        if hasattr(entry, "summary") and entry.summary:
            soup = BeautifulSoup(entry.summary, "html.parser")
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"):
                candidate = self._validate_image_candidate(img_tag["src"], article_url)
                if candidate:
                    return candidate

        # Fallback: fetch page metadata (og:image/twitter:image) only when RSS
        # metadata has no usable image. Guarded by env switch + per-run budget.
        fallback = self._extract_image_from_page_metadata(article_url)
        if fallback:
            logger.debug("Image fallback used from page metadata: %s", article_url)
            return fallback

        return ""

    def _validate_image_candidate(self, candidate: Optional[str], article_url: str) -> str:
        """Normalize/validate an image URL candidate to an absolute public URL."""
        if not candidate:
            return ""
        absolute = make_absolute_url(candidate, article_url) or candidate
        validated = validate_image_url(absolute)
        return validated or ""

    def _extract_image_from_page_metadata(self, article_url: str) -> str:
        """Best-effort OG/Twitter image fallback for RSS entries with no image."""
        if not self._image_fallback_enabled:
            return ""
        if self._image_fallback_budget_remaining <= 0:
            return ""
        parsed = urlparse(article_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""

        self._image_fallback_budget_remaining -= 1
        try:
            from app.extraction.fetcher import fetch_url
            from app.extraction.metadata import extract_metadata

            fetch = fetch_url(article_url)
            if fetch.error or not fetch.html:
                return ""
            metadata = extract_metadata(fetch.html, article_url)
            if metadata.image_url:
                return metadata.image_url
        except Exception as exc:
            logger.debug("Image metadata fallback failed for %s: %s", article_url, exc)

        return ""

    def _parse_date(self, entry) -> datetime:
        """Parse and normalize publication date."""
        if "published_parsed" in entry and entry.published_parsed:
            return datetime(*entry.published_parsed[:6])
        return datetime.utcnow()
