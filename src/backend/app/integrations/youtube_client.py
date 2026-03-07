"""
YouTube RSS feed client.

Handles fetching videos from YouTube channel RSS feeds with role-based
channel configuration for balanced content ingestion.
"""

import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import feedparser
import requests

from app.core.circuit_breaker import CircuitBreaker, get_youtube_breaker
from app.core.logging import get_logger
from app.integrations.youtube_channels import (
    ChannelConfig,
    ChannelRole,
    ContentFormat,
    QualityTier,
    get_enabled_channels,
    get_long_form_channels,
    get_shorts_channels,
)

logger = get_logger(__name__)


# Extended category keywords for video classification
CATEGORY_KEYWORDS = {
    # Core tech categories
    "Artificial Intelligence": [
        "ai",
        "artificial intelligence",
        "machine learning",
        "chatgpt",
        "openai",
        "gpt",
        "neural",
        "deep learning",
        "llm",
        "large language model",
        "claude",
        "gemini",
        "copilot",
        "midjourney",
        "stable diffusion",
        "generative ai",
    ],
    "Mobile": [
        "iphone",
        "android",
        "samsung",
        "pixel",
        "smartphone",
        "phone",
        "mobile",
        "galaxy",
        "oneplus",
        "xiaomi",
        "oppo",
        "ios",
        "tablet",
        "ipad",
    ],
    "Gaming": [
        "game",
        "gaming",
        "playstation",
        "xbox",
        "nintendo",
        "ps5",
        "steam",
        "switch",
        "esports",
        "fps",
        "rpg",
        "mmorpg",
        "console",
        "pc gaming",
    ],
    "Hardware": [
        "cpu",
        "gpu",
        "processor",
        "laptop",
        "pc build",
        "computer",
        "chip",
        "hardware",
        "motherboard",
        "ram",
        "ssd",
        "nvme",
        "intel",
        "amd",
        "nvidia",
        "rtx",
        "radeon",
        "ryzen",
        "apple silicon",
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
    ],
    "Software": [
        "app",
        "software",
        "windows",
        "macos",
        "linux",
        "update",
        "feature",
        "operating system",
        "browser",
        "chrome",
        "firefox",
        "safari",
    ],
    "Reviews": [
        "review",
        "unboxing",
        "hands on",
        "first look",
        "comparison",
        "vs",
        "versus",
        "best",
        "worst",
        "tested",
    ],
    # New expanded categories
    "Cloud & Infrastructure": [
        "cloud",
        "aws",
        "azure",
        "gcp",
        "google cloud",
        "kubernetes",
        "docker",
        "serverless",
        "microservices",
        "devops",
        "infrastructure",
        "data center",
    ],
    "Cybersecurity": [
        "security",
        "cybersecurity",
        "hack",
        "breach",
        "vulnerability",
        "malware",
        "ransomware",
        "privacy",
        "vpn",
        "encryption",
        "zero day",
        "exploit",
    ],
    "Startups & Business": [
        "startup",
        "funding",
        "vc",
        "venture capital",
        "acquisition",
        "ipo",
        "valuation",
        "unicorn",
        "series a",
        "seed round",
        "tech industry",
    ],
    "AI Tools": [
        "ai tool",
        "prompt",
        "automation",
        "no code",
        "low code",
        "saas",
        "productivity",
        "workflow",
        "ai assistant",
        "agent",
        "chatbot",
    ],
    "Developer & Engineering": [
        "developer",
        "programming",
        "coding",
        "api",
        "sdk",
        "framework",
        "javascript",
        "python",
        "rust",
        "typescript",
        "react",
        "vue",
        "node",
        "backend",
        "frontend",
        "fullstack",
        "database",
        "sql",
    ],
}


@dataclass
class VideoEntry:
    """Represents a parsed YouTube video entry with enhanced metadata."""

    title: str
    video_url: str
    thumbnail_url: str
    summary: str
    source: str  # Channel name
    category: str
    video_id: str
    # Enhanced metadata
    channel_id: str = ""
    channel_role: Optional[ChannelRole] = None
    content_format: Optional[ContentFormat] = None
    quality_tier: Optional[QualityTier] = None
    is_short: bool = False


class YouTubeClient:
    """
    Client for fetching videos from YouTube RSS feeds.

    Uses role-based channel configuration for balanced content ingestion.
    Supports separate pipelines for long-form videos and shorts/reels.
    """

    def __init__(
        self,
        channel_configs: Optional[List[ChannelConfig]] = None,
        daily_video_cap_per_channel: int = 2,
        daily_shorts_cap_per_channel: int = 4,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """
        Initialize YouTube client with channel configuration.

        Args:
            channel_configs: List of ChannelConfig objects. Defaults to enabled channels.
            daily_video_cap_per_channel: Default cap for long-form videos per channel
            daily_shorts_cap_per_channel: Default cap for shorts per channel
            circuit_breaker: Optional CircuitBreaker instance. Defaults to shared singleton.
        """
        self.channel_configs = channel_configs or get_enabled_channels()
        self.daily_video_cap = daily_video_cap_per_channel
        self.daily_shorts_cap = daily_shorts_cap_per_channel

        # Circuit breaker for external HTTP calls
        self._breaker = circuit_breaker or get_youtube_breaker()

        # Track ingestion counts per channel per day
        self._daily_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"videos": 0, "shorts": 0}
        )
        self._last_reset: datetime = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Best-effort cache to avoid repeated scraping.
        self._duration_cache_seconds: Dict[str, Optional[int]] = {}
        # Shared retry budget for feed fetches in this client instance/run.
        self._retry_budget_remaining = max(0, int(os.getenv("CONNECTOR_RETRY_BUDGET", "40")))
        self._max_retries = max(0, int(os.getenv("CONNECTOR_MAX_RETRIES", "2")))
        self._timeout_seconds = max(1.0, float(os.getenv("CONNECTOR_TIMEOUT_SECONDS", "15")))
        self._backoff_base_seconds = max(
            0.0, float(os.getenv("CONNECTOR_BACKOFF_BASE_SECONDS", "0.5"))
        )

    def _reset_daily_counts_if_needed(self):
        """Reset daily counts at UTC midnight."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if today > self._last_reset:
            self._daily_counts.clear()
            self._last_reset = today

    def get_transcript(self, video_id: str) -> Optional[str]:
        """
        DEPRECATED: Transcript fetching has been removed for YouTube ToS compliance.

        Previously used youtube-transcript-api which violates YouTube ToS.
        Summaries now rely on RSS feed descriptions and YouTube Data API snippets.

        Args:
            video_id: The YouTube video ID.

        Returns:
            Always returns None.
        """
        # Removed: youtube-transcript-api violates YouTube ToS
        # See SECURITY.md section 6 for details
        return None

    def is_youtube_short(self, video_id: str) -> bool:
        """
        Check if a video is a YouTube Short.

        Args:
            video_id: The YouTube video ID.

        Returns:
            True if it's a Short, False otherwise.
        """
        if not self._breaker.allow_request():
            logger.debug("Circuit open - skipping short check for %s", video_id)
            return False
        url = f"https://www.youtube.com/shorts/{video_id}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            response = requests.head(url, headers=headers, allow_redirects=False, timeout=5)
            self._breaker.record_success()
            return response.status_code == 200
        except Exception as e:
            self._breaker.record_failure()
            logger.debug(f"Error checking if video {video_id} is a Short: {e}")
            return False

    def get_video_duration(self, video_id: str) -> Optional[int]:
        """
        Get video duration in seconds.

        Uses YouTube Data API if available (YOUTUBE_API_KEY env var),
        otherwise falls back to scraping (less reliable due to consent pages).

        Args:
            video_id: The YouTube video ID.

        Returns:
            Duration in seconds or None if not found.
        """
        if video_id in self._duration_cache_seconds:
            cached = self._duration_cache_seconds[video_id]
            logger.debug("get_video_duration: vid=%s -> %s (cached)", video_id, cached)
            return cached

        # Try YouTube Data API first (if API key available)
        api_key = os.getenv("YOUTUBE_API_KEY")
        if api_key:
            duration = self._get_duration_from_api(video_id, api_key)
            if duration is not None:
                self._duration_cache_seconds[video_id] = duration
                return duration

        # Fall back to scraping (less reliable due to consent pages in Docker/server)
        duration = self._get_duration_from_scrape(video_id)
        if duration is not None:
            self._duration_cache_seconds[video_id] = duration
            return duration

        self._duration_cache_seconds[video_id] = None
        return None

    def _get_duration_from_api(self, video_id: str, api_key: str) -> Optional[int]:
        """Get duration using YouTube Data API."""
        if not self._breaker.allow_request():
            logger.debug("Circuit open - skipping API duration for %s", video_id)
            return None
        try:
            url = f"https://www.googleapis.com/youtube/v3/videos?part=contentDetails&id={video_id}&key={api_key}"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                self._breaker.record_success()
                data = response.json()
                items = data.get("items", [])
                if items:
                    duration_iso = items[0].get("contentDetails", {}).get("duration", "")
                    seconds = self._parse_iso_duration(duration_iso)
                    if seconds is not None:
                        logger.info(
                            "get_video_duration: vid=%s -> %d seconds (API)", video_id, seconds
                        )
                        return seconds
            else:
                self._breaker.record_failure()
                logger.warning(
                    "get_video_duration: API error HTTP %d for vid=%s",
                    response.status_code,
                    video_id,
                )
        except Exception as e:
            self._breaker.record_failure()
            logger.warning("get_video_duration: API exception for vid=%s: %s", video_id, str(e))
        return None

    def _parse_iso_duration(self, duration: str) -> Optional[int]:
        """Parse ISO 8601 duration format (PT1H2M3S) to seconds."""
        if not duration or not duration.startswith("PT"):
            return None

        pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
        match = re.match(pattern, duration)
        if not match:
            return None

        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    def _get_duration_from_scrape(self, video_id: str) -> Optional[int]:
        """Get duration by scraping YouTube page (fallback, less reliable)."""
        if not self._breaker.allow_request():
            logger.debug("Circuit open - skipping scrape duration for %s", video_id)
            return None
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                self._breaker.record_success()
                # Try multiple patterns for duration
                patterns = [
                    (
                        r'"approxDurationMs":"(\d+)"',
                        "approxDurationMs",
                        lambda m: int(m.group(1)) // 1000,
                    ),
                    (r'"lengthSeconds":"(\d+)"', "lengthSeconds", lambda m: int(m.group(1))),
                ]

                for pattern, name, extractor in patterns:
                    match = re.search(pattern, response.text)
                    if match:
                        seconds = extractor(match)
                        logger.info(
                            "get_video_duration: vid=%s -> %d seconds (scrape/%s)",
                            video_id,
                            seconds,
                            name,
                        )
                        return seconds

                logger.debug(
                    "get_video_duration: vid=%s -> None (scrape: no pattern matched)", video_id
                )
            else:
                self._breaker.record_failure()
                logger.debug(
                    "get_video_duration: vid=%s -> None (scrape: HTTP %d)",
                    video_id,
                    response.status_code,
                )
        except Exception as e:
            self._breaker.record_failure()
            logger.debug("get_video_duration: vid=%s -> None (scrape error: %s)", video_id, str(e))
        return None

    def _is_vertical_thumbnail(self, video_id: str) -> Optional[bool]:
        """
        Check if a video has a vertical thumbnail (indicating it's a Short).

        NOTE: This method is currently limited. YouTube's oembed endpoint always
        returns the standard hqdefault.jpg thumbnail (480x360) regardless of
        whether the video is a Short. True aspect ratio detection would require
        either:
        1. YouTube Data API (contentDetails part)
        2. Downloading and analyzing the actual video file
        3. Web scraping (blocked by consent pages in Docker/server environments)

        Args:
            video_id: The YouTube video ID.

        Returns:
            True if vertical (likely Short), False if horizontal, None if detection failed.
        """
        # The oembed endpoint doesn't reliably indicate Shorts - it always returns
        # horizontal thumbnail dimensions (480x360). This method is kept as a stub
        # for future improvements if YouTube provides better metadata.
        #
        # For now, return None to indicate detection failed, which causes the
        # caller to fall back to other methods or default assumptions.
        logger.debug(
            "_is_vertical_thumbnail: vid=%s -> None (oembed unreliable for aspect ratio)", video_id
        )
        return None

    def fetch_all_channels(self, videos_per_channel: int = 10) -> List[VideoEntry]:
        """
        Fetch videos from all configured channels with role-based caps.

        This is the main entry point for video ingestion.
        Fetches both long-form and shorts content.

        Args:
            videos_per_channel: Maximum videos to fetch per channel per request

        Returns:
            List of VideoEntry objects with enhanced metadata
        """
        self._reset_daily_counts_if_needed()

        all_videos = []
        role_counts: Dict[ChannelRole, int] = defaultdict(int)

        for config in self.channel_configs:
            try:
                # Determine effective cap based on channel config
                effective_cap = min(
                    videos_per_channel, config.daily_cap * 2
                )  # Fetch more, filter later

                channel_videos = self._fetch_channel_with_config(config, effective_cap)

                for video in channel_videos:
                    all_videos.append(video)
                    if video.channel_role:
                        role_counts[video.channel_role] += 1

            except Exception as e:
                logger.error(f"Error fetching YouTube feed for {config.name}: {str(e)}")

        # Log role distribution
        logger.info(f"Total videos fetched: {len(all_videos)}")
        for role, count in role_counts.items():
            logger.info(f"  {role.value}: {count} videos")

        return all_videos

    def fetch_long_form_videos(self, videos_per_channel: int = 5) -> List[VideoEntry]:
        """
        Fetch only long-form videos (> 3 minutes) from appropriate channels.

        Returns videos suitable for the Videos tab.
        """
        self._reset_daily_counts_if_needed()

        videos = []
        long_form_channels = get_long_form_channels()

        for config in long_form_channels:
            try:
                channel_videos = self._fetch_channel_with_config(config, videos_per_channel)

                # Filter to long-form only
                for video in channel_videos:
                    if not video.is_short:
                        videos.append(video)

            except Exception as e:
                logger.error(f"Error fetching long-form from {config.name}: {str(e)}")

        logger.info(f"Fetched {len(videos)} long-form videos")
        return videos

    def fetch_shorts(self, shorts_per_channel: int = 5) -> List[VideoEntry]:
        """
        Fetch only shorts/reels from appropriate channels.

        Returns videos suitable for the Reels tab.
        """
        self._reset_daily_counts_if_needed()

        shorts = []
        shorts_channels = get_shorts_channels()

        for config in shorts_channels:
            try:
                channel_videos = self._fetch_channel_with_config(config, shorts_per_channel)

                # Filter to shorts only
                for video in channel_videos:
                    if video.is_short:
                        shorts.append(video)

            except Exception as e:
                logger.error(f"Error fetching shorts from {config.name}: {str(e)}")

        logger.info(f"Fetched {len(shorts)} shorts/reels")
        return shorts

    def _fetch_channel_with_config(
        self, config: ChannelConfig, max_videos: int
    ) -> List[VideoEntry]:
        """
        Fetch videos from a single channel using its configuration.

        Args:
            config: Channel configuration
            max_videos: Maximum videos to fetch

        Returns:
            List of VideoEntry objects with metadata
        """
        videos = []

        try:
            logger.info(
                "Fetching YouTube feed: %s (content_format=%s)",
                config.name,
                config.content_format.value if config.content_format else "None",
            )
            feed = self._parse_feed_with_retries(config.feed_url)

            if feed.bozo:
                logger.warning(f"Feed parsing issue for {config.feed_url}: {feed.bozo_exception}")

            channel_name = feed.feed.get("title", config.name)
            logger.info(f"Channel: {channel_name}, Entries found: {len(feed.entries)}")

            for entry in feed.entries[:max_videos]:
                try:
                    video_url = entry.get("link", "")
                    if not video_url:
                        continue

                    video_id = self._extract_video_id(video_url)
                    if not video_id:
                        continue

                    title = entry.get("title", "Untitled")

                    # Check if it's a short
                    is_short = self._detect_short(video_url, video_id, config, title=title)

                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    summary = self._get_summary(entry)
                    category = self._categorize_video(title, summary)

                    videos.append(
                        VideoEntry(
                            title=title,
                            video_url=video_url,
                            thumbnail_url=thumbnail_url,
                            summary=summary,
                            source=channel_name,
                            category=category,
                            video_id=video_id,
                            channel_id=config.channel_id,
                            channel_role=config.role,
                            content_format=config.content_format,
                            quality_tier=config.quality_tier,
                            is_short=is_short,
                        )
                    )

                    logger.info(f"Added video: {title}")

                except Exception as e:
                    logger.error(f"Error processing video entry: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing YouTube feed {config.feed_url}: {str(e)}")
            raise

        return videos

    def _detect_short(
        self,
        video_url: str,
        video_id: str,
        config: ChannelConfig,
        title: str = "",
    ) -> bool:
        """
        Detect if a video is a short based on multiple signals.

        Args:
            video_url: Video URL
            video_id: Video ID
            config: Channel configuration
            title: Video title (for hashtag detection)

        Returns:
            True if video is a short
        """
        # URL contains /shorts/
        if "/shorts/" in video_url:
            logger.info("_detect_short: vid=%s -> True (url contains /shorts/)", video_id)
            return True

        # Channel is shorts-native
        if config.content_format == ContentFormat.SHORTS:
            logger.info("_detect_short: vid=%s -> True (channel is SHORTS format)", video_id)
            return True

        # Title contains #shorts or #short hashtag (common for shorts)
        title_lower = title.lower()
        if "#shorts" in title_lower or "#short" in title_lower:
            logger.info("_detect_short: vid=%s -> True (title contains #shorts)", video_id)
            return True

        # For mixed channels, use duration heuristic.
        # YouTube RSS links are commonly /watch?v=... for both videos and Shorts,
        # so URL-only detection is unreliable.
        if config.content_format == ContentFormat.MIXED:
            short_max_seconds = int(os.getenv("YT_SHORT_MAX_SECONDS", "75"))

            # Try duration (requires API key - scraping blocked in Docker by consent pages)
            duration = self.get_video_duration(video_id)
            if duration is not None:
                if duration <= short_max_seconds:
                    logger.info(
                        "_detect_short: vid=%s -> True (MIXED, duration=%ds <= %ds)",
                        video_id,
                        duration,
                        short_max_seconds,
                    )
                    return True
                else:
                    logger.info(
                        "_detect_short: vid=%s -> False (MIXED, duration=%ds > %ds)",
                        video_id,
                        duration,
                        short_max_seconds,
                    )
                    return False

            # Duration detection failed (no API key and scraping blocked)
            # NOTE: Thumbnail aspect ratio detection via oembed doesn't work - YouTube
            # always returns horizontal (480x360) thumbnails regardless of video type.
            # Without YOUTUBE_API_KEY, we cannot reliably detect Shorts for MIXED channels.
            logger.info(
                "_detect_short: vid=%s -> False (MIXED, duration detection failed - set YOUTUBE_API_KEY for reliable detection)",
                video_id,
            )
            return False

        logger.info("_detect_short: vid=%s -> False (LONG_FORM channel)", video_id)
        return False

    # Legacy method for backward compatibility
    def fetch_channel(self, feed_url: str, max_videos: int = 5) -> List[VideoEntry]:
        """
        Fetch videos from a single YouTube channel feed (legacy method).

        Args:
            feed_url: URL of the YouTube channel RSS feed
            max_videos: Maximum videos to return

        Returns:
            List of VideoEntry objects
        """
        # Try to find channel config by URL
        channel_id = self._extract_channel_id_from_url(feed_url)
        config = None

        if channel_id:
            for ch in self.channel_configs:
                if ch.channel_id == channel_id:
                    config = ch
                    break

        if config:
            return self._fetch_channel_with_config(config, max_videos)

        # Fallback to basic fetch without config
        return self._fetch_channel_basic(feed_url, max_videos)

    def _fetch_channel_basic(self, feed_url: str, max_videos: int) -> List[VideoEntry]:
        """Basic channel fetch without configuration (legacy support)."""
        videos = []

        try:
            logger.info(f"Fetching YouTube feed: {feed_url}")
            feed = self._parse_feed_with_retries(feed_url)

            if feed.bozo:
                logger.warning(f"Feed parsing issue for {feed_url}: {feed.bozo_exception}")

            channel_name = feed.feed.get("title", "YouTube")
            logger.info(f"Channel: {channel_name}, Entries found: {len(feed.entries)}")

            for entry in feed.entries[:max_videos]:
                try:
                    video_url = entry.get("link", "")
                    if not video_url:
                        continue

                    video_id = self._extract_video_id(video_url)
                    if not video_id:
                        continue

                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    summary = self._get_summary(entry)
                    title = entry.get("title", "Untitled")
                    category = self._categorize_video(title, summary)

                    videos.append(
                        VideoEntry(
                            title=title,
                            video_url=video_url,
                            thumbnail_url=thumbnail_url,
                            summary=summary,
                            source=channel_name,
                            category=category,
                            video_id=video_id,
                        )
                    )

                    logger.info(f"Added video: {title}")

                except Exception as e:
                    logger.error(f"Error processing video entry: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing YouTube feed {feed_url}: {str(e)}")
            raise

        return videos

    def _parse_feed_with_retries(self, feed_url: str):
        """Fetch + parse a YouTube RSS feed using bounded retry and backoff."""
        if feed_url.lstrip().startswith("<"):
            return feedparser.parse(feed_url)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        attempts = self._max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                if not self._breaker.allow_request():
                    logger.warning("Circuit open - skipping feed fetch for %s", feed_url)
                    break

                response = requests.get(feed_url, headers=headers, timeout=self._timeout_seconds)
                response.raise_for_status()
                self._breaker.record_success()
                return feedparser.parse(response.content)
            except requests.RequestException as exc:
                self._breaker.record_failure()
                last_attempt = attempt >= attempts
                budget_exhausted = self._retry_budget_remaining <= 0
                if last_attempt or budget_exhausted:
                    logger.warning(
                        "YouTube feed fetch failed (url=%s attempt=%s/%s budget_left=%s): %s",
                        feed_url,
                        attempt,
                        attempts,
                        self._retry_budget_remaining,
                        exc,
                    )
                    break

                self._retry_budget_remaining -= 1
                delay = self._backoff_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "YouTube feed retry scheduled (url=%s next_attempt=%s/%s delay=%.2fs budget_left=%s)",
                    feed_url,
                    attempt + 1,
                    attempts,
                    delay,
                    self._retry_budget_remaining,
                )
                if delay > 0:
                    time.sleep(delay)

        # Empty parsed feed keeps call-sites simple and safe.
        return feedparser.parse(b"")

    def _extract_channel_id_from_url(self, url: str) -> Optional[str]:
        """Extract channel ID from feed URL."""
        match = re.search(r"channel_id=([^&]+)", url)
        return match.group(1) if match else None

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL including Shorts URLs."""
        patterns = [
            r"(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)",
            r"youtube\.com\/embed\/([^&\n?#]+)",
            r"youtube\.com\/shorts\/([^&\n?#]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_summary(self, entry) -> str:
        """Extract summary from feed entry."""
        max_length = 5000
        description = ""

        # Try media_group description
        if hasattr(entry, "media_group") and entry.media_group:
            for media in entry.media_group:
                if hasattr(media, "media_description"):
                    desc = media.media_description
                    if desc:
                        description = desc
                        break

        # Try summary field
        if not description and hasattr(entry, "summary") and entry.summary:
            description = entry.summary

        # Try description field
        if not description and hasattr(entry, "description") and entry.description:
            description = entry.description

        if description:
            return description[:max_length]

        # Try to get transcript if description is missing
        video_url = entry.get("link", "")
        video_id = self._extract_video_id(video_url)
        if video_id:
            transcript = self.get_transcript(video_id)
            if transcript:
                return transcript[:max_length]

        # Default summary
        author = entry.get("author", "YouTube")
        title = entry.get("title", "this video")
        return f"Watch this video from {author} about {title}"

    def _categorize_video(self, title: str, summary: str) -> str:
        """Categorize video based on keywords in title and summary."""
        text = (title + " " + summary).lower()

        # Score each category
        category_scores: Dict[str, int] = defaultdict(int)

        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    category_scores[category] += 1

        if category_scores:
            # Return category with highest score
            return max(category_scores, key=category_scores.get)

        return "Technology"

    @staticmethod
    def get_thumbnail_url(video_id: str, quality: str = "maxresdefault") -> str:
        """
        Get thumbnail URL for a video ID.

        Args:
            video_id: YouTube video ID
            quality: Thumbnail quality (maxresdefault, hqdefault, mqdefault, sddefault)

        Returns:
            Thumbnail URL
        """
        return f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"

    def get_role_distribution(self, videos: List[VideoEntry]) -> Dict[str, int]:
        """Get distribution of videos by role."""
        distribution: Dict[str, int] = defaultdict(int)
        for video in videos:
            role = video.channel_role.value if video.channel_role else "unknown"
            distribution[role] += 1
        return dict(distribution)

    def get_format_distribution(self, videos: List[VideoEntry]) -> Dict[str, int]:
        """Get distribution of videos by format (long-form vs shorts)."""
        return {
            "long_form": len([v for v in videos if not v.is_short]),
            "shorts": len([v for v in videos if v.is_short]),
        }
