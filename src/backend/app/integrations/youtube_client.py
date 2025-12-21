"""
YouTube RSS feed client.

Handles fetching videos from YouTube channel RSS feeds.
"""

import feedparser
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from app.core.logging import get_logger

logger = get_logger(__name__)


# Default YouTube channel feeds for tech content
DEFAULT_CHANNEL_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ",  # MKBHD
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCXuqSBlHAE6Xw-yeJA0Tunw",  # Linus Tech Tips
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCddiUEpeqJcYeBxX1IVBKvQ",  # The Verge
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCVYamHliCI9rw1tHR1xbkfw",  # Dave2D
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCsTcErHg8oDvUnTzoqsYeNw",  # Unbox Therapy
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCXGgrKt94gR6lmN4aN3mYTg",  # Austin Evans
]

# Category keywords for video classification
CATEGORY_KEYWORDS = {
    "Artificial Intelligence": ["ai", "artificial intelligence", "machine learning", "chatgpt", "openai", "gpt", "neural", "deep learning"],
    "Mobile": ["iphone", "android", "samsung", "pixel", "smartphone", "phone", "mobile"],
    "Gaming": ["game", "gaming", "playstation", "xbox", "nintendo", "ps5", "steam"],
    "Hardware": ["cpu", "gpu", "processor", "laptop", "pc build", "computer", "chip", "hardware"],
    "Software": ["app", "software", "windows", "macos", "linux", "update", "feature"],
    "Reviews": ["review", "unboxing", "hands on", "first look", "comparison"],
}


@dataclass
class VideoEntry:
    """Represents a parsed YouTube video entry."""
    title: str
    video_url: str
    thumbnail_url: str
    summary: str
    source: str  # Channel name
    category: str
    video_id: str


class YouTubeClient:
    """
    Client for fetching videos from YouTube RSS feeds.
    
    YouTube provides RSS feeds for channels at:
    https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID
    """
    
    def __init__(self, channel_feeds: Optional[List[str]] = None):
        """
        Initialize YouTube client.
        
        Args:
            channel_feeds: List of YouTube channel RSS feed URLs.
                         Defaults to DEFAULT_CHANNEL_FEEDS
        """
        self.channel_feeds = channel_feeds or DEFAULT_CHANNEL_FEEDS
    
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
            # Combine all text parts
            full_text = " ".join([item['text'] for item in transcript_list])
            return full_text
        except (TranscriptsDisabled, NoTranscriptFound):
            logger.warning(f"No transcript available for video {video_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching transcript for video {video_id}: {e}")
            return None

    def fetch_all_channels(self, videos_per_channel: int = 5) -> List[VideoEntry]:
        """
        Fetch videos from all configured channels.
        
        Args:
            videos_per_channel: Maximum videos to fetch per channel
            
        Returns:
            List of VideoEntry objects
        """
        videos = []
        
        for feed_url in self.channel_feeds:
            try:
                channel_videos = self.fetch_channel(feed_url, videos_per_channel)
                videos.extend(channel_videos)
            except Exception as e:
                logger.error(f"Error fetching YouTube feed {feed_url}: {str(e)}")
        
        logger.info(f"Total videos fetched: {len(videos)}")
        return videos
    
    def fetch_channel(self, feed_url: str, max_videos: int = 5) -> List[VideoEntry]:
        """
        Fetch videos from a single YouTube channel feed.
        
        Args:
            feed_url: URL of the YouTube channel RSS feed
            max_videos: Maximum videos to return
            
        Returns:
            List of VideoEntry objects
        """
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
                        video_id=video_id
                    ))
                    
                    logger.info(f"Added video: {title}")
                    
                except Exception as e:
                    logger.error(f"Error processing video entry: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error parsing YouTube feed {feed_url}: {str(e)}")
            raise
        
        return videos
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)',
            r'youtube\.com\/embed\/([^&\n?#]+)',
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
        
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                return category
        
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
