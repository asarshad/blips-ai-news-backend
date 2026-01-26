"""
YouTube RSS feed client.

Handles fetching videos from YouTube channel RSS feeds with role-based
channel configuration for balanced content ingestion.
"""

import feedparser
import re
import requests
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from app.core.logging import get_logger
from app.integrations.youtube_channels import (
    ChannelConfig,
    ChannelRole,
    ContentFormat,
    QualityTier,
    get_enabled_channels,
    get_long_form_channels,
    get_shorts_channels,
    get_channel_by_name,
    get_quality_weight_modifier,
)

logger = get_logger(__name__)


# Extended category keywords for video classification
CATEGORY_KEYWORDS = {
    # Core tech categories
    "Artificial Intelligence": [
        "ai", "artificial intelligence", "machine learning", "chatgpt", "openai", 
        "gpt", "neural", "deep learning", "llm", "large language model", "claude",
        "gemini", "copilot", "midjourney", "stable diffusion", "generative ai"
    ],
    "Mobile": [
        "iphone", "android", "samsung", "pixel", "smartphone", "phone", "mobile",
        "galaxy", "oneplus", "xiaomi", "oppo", "ios", "tablet", "ipad"
    ],
    "Gaming": [
        "game", "gaming", "playstation", "xbox", "nintendo", "ps5", "steam",
        "switch", "esports", "fps", "rpg", "mmorpg", "console", "pc gaming"
    ],
    "Hardware": [
        "cpu", "gpu", "processor", "laptop", "pc build", "computer", "chip", 
        "hardware", "motherboard", "ram", "ssd", "nvme", "intel", "amd", "nvidia",
        "rtx", "radeon", "ryzen", "apple silicon", "m1", "m2", "m3", "m4", "m5"
    ],
    "Software": [
        "app", "software", "windows", "macos", "linux", "update", "feature",
        "operating system", "browser", "chrome", "firefox", "safari"
    ],
    "Reviews": [
        "review", "unboxing", "hands on", "first look", "comparison", 
        "vs", "versus", "best", "worst", "tested"
    ],
    # New expanded categories
    "Cloud & Infrastructure": [
        "cloud", "aws", "azure", "gcp", "google cloud", "kubernetes", "docker",
        "serverless", "microservices", "devops", "infrastructure", "data center"
    ],
    "Cybersecurity": [
        "security", "cybersecurity", "hack", "breach", "vulnerability", "malware",
        "ransomware", "privacy", "vpn", "encryption", "zero day", "exploit"
    ],
    "Startups & Business": [
        "startup", "funding", "vc", "venture capital", "acquisition", "ipo",
        "valuation", "unicorn", "series a", "seed round", "tech industry"
    ],
    "AI Tools": [
        "ai tool", "prompt", "automation", "no code", "low code", "saas",
        "productivity", "workflow", "ai assistant", "agent", "chatbot"
    ],
    "Developer & Engineering": [
        "developer", "programming", "coding", "api", "sdk", "framework",
        "javascript", "python", "rust", "typescript", "react", "vue", "node",
        "backend", "frontend", "fullstack", "database", "sql"
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
    ):
        """
        Initialize YouTube client with channel configuration.
        
        Args:
            channel_configs: List of ChannelConfig objects. Defaults to enabled channels.
            daily_video_cap_per_channel: Default cap for long-form videos per channel
            daily_shorts_cap_per_channel: Default cap for shorts per channel
        """
        self.channel_configs = channel_configs or get_enabled_channels()
        self.daily_video_cap = daily_video_cap_per_channel
        self.daily_shorts_cap = daily_shorts_cap_per_channel
        
        # Track ingestion counts per channel per day
        self._daily_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"videos": 0, "shorts": 0})
        self._last_reset: datetime = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    def _reset_daily_counts_if_needed(self):
        """Reset daily counts at UTC midnight."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if today > self._last_reset:
            self._daily_counts.clear()
            self._last_reset = today
    
    def get_transcript(self, video_id: str) -> Optional[str]:
        """
        Fetches the transcript for a YouTube video.
        
        Args:
            video_id: The YouTube video ID.
            
        Returns:
            The transcript text or None if not found/disabled.
        """
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join([item['text'] for item in transcript_list])
            return full_text
        except (TranscriptsDisabled, NoTranscriptFound):
            logger.warning(f"No transcript available for video {video_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching transcript for video {video_id}: {e}")
            return None

    def is_youtube_short(self, video_id: str) -> bool:
        """
        Check if a video is a YouTube Short.
        
        Args:
            video_id: The YouTube video ID.
            
        Returns:
            True if it's a Short, False otherwise.
        """
        url = f'https://www.youtube.com/shorts/{video_id}'
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            response = requests.head(url, headers=headers, allow_redirects=False, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Error checking if video {video_id} is a Short: {e}")
            return False

    def get_video_duration(self, video_id: str) -> Optional[int]:
        """
        Get video duration in seconds by scraping the video page.
        
        Args:
            video_id: The YouTube video ID.
            
        Returns:
            Duration in seconds or None if not found.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                match = re.search(r'"approxDurationMs":"(\d+)"', response.text)
                if match:
                    ms = int(match.group(1))
                    return ms // 1000
        except Exception as e:
            logger.debug(f"Error fetching duration for video {video_id}: {e}")
        
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
                effective_cap = min(videos_per_channel, config.daily_cap * 2)  # Fetch more, filter later
                
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
        self, 
        config: ChannelConfig, 
        max_videos: int
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
            logger.info(f"Fetching YouTube feed: {config.feed_url}")
            feed = feedparser.parse(config.feed_url)
            
            if feed.bozo:
                logger.warning(f"Feed parsing issue for {config.feed_url}: {feed.bozo_exception}")
            
            channel_name = feed.feed.get('title', config.name)
            logger.info(f"Channel: {channel_name}, Entries found: {len(feed.entries)}")
            
            for entry in feed.entries[:max_videos]:
                try:
                    video_url = entry.get('link', '')
                    if not video_url:
                        continue
                    
                    video_id = self._extract_video_id(video_url)
                    if not video_id:
                        continue
                    
                    # Check if it's a short
                    is_short = self._detect_short(video_url, video_id, config)
                    
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    summary = self._get_summary(entry)
                    title = entry.get('title', 'Untitled')
                    category = self._categorize_video(title, summary)
                    
                    videos.append(VideoEntry(
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
                    ))
                    
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
        config: ChannelConfig
    ) -> bool:
        """
        Detect if a video is a short based on multiple signals.
        
        Args:
            video_url: Video URL
            video_id: Video ID
            config: Channel configuration
            
        Returns:
            True if video is a short
        """
        # URL contains /shorts/
        if "/shorts/" in video_url:
            return True
        
        # Channel is shorts-native
        if config.content_format == ContentFormat.SHORTS:
            return True
        
        # For mixed channels, check the shorts URL
        if config.content_format == ContentFormat.MIXED:
            return self.is_youtube_short(video_id)
        
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
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                logger.warning(f"Feed parsing issue for {feed_url}: {feed.bozo_exception}")
            
            channel_name = feed.feed.get('title', 'YouTube')
            logger.info(f"Channel: {channel_name}, Entries found: {len(feed.entries)}")
            
            for entry in feed.entries[:max_videos]:
                try:
                    video_url = entry.get('link', '')
                    if not video_url:
                        continue
                    
                    video_id = self._extract_video_id(video_url)
                    if not video_id:
                        continue
                    
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    summary = self._get_summary(entry)
                    title = entry.get('title', 'Untitled')
                    category = self._categorize_video(title, summary)
                    
                    videos.append(VideoEntry(
                        title=title,
                        video_url=video_url,
                        thumbnail_url=thumbnail_url,
                        summary=summary,
                        source=channel_name,
                        category=category,
                        video_id=video_id,
                    ))
                    
                    logger.info(f"Added video: {title}")
                    
                except Exception as e:
                    logger.error(f"Error processing video entry: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error parsing YouTube feed {feed_url}: {str(e)}")
            raise
        
        return videos
    
    def _extract_channel_id_from_url(self, url: str) -> Optional[str]:
        """Extract channel ID from feed URL."""
        match = re.search(r'channel_id=([^&]+)', url)
        return match.group(1) if match else None
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL including Shorts URLs."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)',
            r'youtube\.com\/embed\/([^&\n?#]+)',
            r'youtube\.com\/shorts\/([^&\n?#]+)',
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
        if hasattr(entry, 'media_group') and entry.media_group:
            for media in entry.media_group:
                if hasattr(media, 'media_description'):
                    desc = media.media_description
                    if desc:
                        description = desc
                        break
        
        # Try summary field
        if not description and hasattr(entry, 'summary') and entry.summary:
            description = entry.summary
        
        # Try description field
        if not description and hasattr(entry, 'description') and entry.description:
            description = entry.description
            
        if description:
            return description[:max_length]
        
        # Try to get transcript if description is missing
        video_url = entry.get('link', '')
        video_id = self._extract_video_id(video_url)
        if video_id:
            transcript = self.get_transcript(video_id)
            if transcript:
                return transcript[:max_length]
        
        # Default summary
        author = entry.get('author', 'YouTube')
        title = entry.get('title', 'this video')
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
