
import feedparser
import requests
from app.core.config import settings
from app.core.logging import get_logger
from app.models.video import Video
from app.repositories.video_repo import VideoRepository
from typing import List, Dict, Any
import re

logger = get_logger(__name__)

# YouTube channel RSS feeds for tech content
YOUTUBE_CHANNEL_FEEDS = [
    # Tech news channels - verified channel IDs
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ",  # MKBHD (Marques Brownlee)
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCXuqSBlHAE6Xw-yeJA0Tunw",  # Linus Tech Tips
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCddiUEpeqJcYeBxX1IVBKvQ",  # The Verge
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCVYamHliCI9rw1tHR1xbkfw",  # Dave2D
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCsTcErHg8oDvUnTzoqsYeNw",  # Unbox Therapy
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCXGgrKt94gR6lmN4aN3mYTg",  # Austin Evans
]


class VideoFetcher:
    def __init__(self, video_repo: VideoRepository):
        self.video_repo = video_repo
        self.feeds = YOUTUBE_CHANNEL_FEEDS

    def fetch_latest_videos(self) -> List[Dict[str, Any]]:
        """Fetch latest videos from YouTube RSS feeds"""
        videos = []
        
        for feed_url in self.feeds:
            try:
                logger.info(f"Fetching YouTube feed: {feed_url}")
                feed = feedparser.parse(feed_url)
                
                if feed.bozo:
                    logger.warning(f"Feed parsing issue for {feed_url}: {feed.bozo_exception}")
                
                channel_name = feed.feed.get('title', 'YouTube')
                logger.info(f"Channel: {channel_name}, Entries found: {len(feed.entries)}")
                
                for entry in feed.entries[:5]:  # Get top 5 from each channel
                    video_url = entry.get('link', '')
                    if not video_url:
                        logger.warning(f"No link found for entry: {entry.get('title', 'Unknown')}")
                        continue
                    
                    # Check if video already exists
                    existing = self.video_repo.get_by_url(video_url)
                    if existing:
                        logger.debug(f"Video already exists: {video_url}")
                        continue
                    
                    # Extract video ID for thumbnail
                    video_id = self._extract_video_id(video_url)
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg" if video_id else None
                    
                    # Get description/summary
                    summary = self._get_summary(entry)
                    
                    video_data = {
                        "title": entry.get('title', 'Untitled'),
                        "summary": summary,
                        "video_url": video_url,
                        "source_url": video_url,
                        "thumbnail_url": thumbnail_url,
                        "source": channel_name,
                        "category": self._categorize_video(entry.get('title', ''), summary),
                    }
                    
                    videos.append(video_data)
                    logger.info(f"Added video: {video_data['title']}")
                    
            except Exception as e:
                logger.error(f"Error fetching YouTube feed {feed_url}: {str(e)}", exc_info=True)
        
        logger.info(f"Total videos fetched: {len(videos)}")
        return videos

    def _extract_video_id(self, url: str) -> str:
        """Extract YouTube video ID from URL"""
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
        """Extract summary from feed entry"""
        # Try different fields for summary
        if hasattr(entry, 'media_group') and entry.media_group:
            for media in entry.media_group:
                if hasattr(media, 'media_description'):
                    desc = media.media_description
                    if desc:
                        # Limit to first 500 chars
                        return desc[:500] + "..." if len(desc) > 500 else desc
        
        if hasattr(entry, 'summary'):
            return entry.summary[:500] if len(entry.summary) > 500 else entry.summary
        
        if hasattr(entry, 'description'):
            return entry.description[:500] if len(entry.description) > 500 else entry.description
        
        return f"Watch this video from {entry.get('author', 'YouTube')} about {entry.title}"

    def _categorize_video(self, title: str, summary: str) -> str:
        """Simple categorization based on keywords"""
        text = (title + " " + summary).lower()
        
        categories = {
            "Artificial Intelligence": ["ai", "artificial intelligence", "machine learning", "chatgpt", "openai", "gpt", "neural", "deep learning"],
            "Mobile": ["iphone", "android", "samsung", "pixel", "smartphone", "phone", "mobile"],
            "Gaming": ["game", "gaming", "playstation", "xbox", "nintendo", "ps5", "steam"],
            "Hardware": ["cpu", "gpu", "processor", "laptop", "pc build", "computer", "chip", "hardware"],
            "Software": ["app", "software", "windows", "macos", "linux", "update", "feature"],
            "Reviews": ["review", "unboxing", "hands on", "first look", "comparison"],
        }
        
        for category, keywords in categories.items():
            if any(keyword in text for keyword in keywords):
                return category
        
        return "Technology"

    def save_videos(self, videos: List[Dict[str, Any]]) -> int:
        """Save fetched videos to database"""
        saved_count = 0
        
        for video_data in videos:
            try:
                # Double-check it doesn't exist
                existing = self.video_repo.get_by_url(video_data["video_url"])
                if existing:
                    continue
                
                video = Video(
                    title=video_data["title"],
                    summary=video_data["summary"],
                    video_url=video_data["video_url"],
                    source_url=video_data["source_url"],
                    thumbnail_url=video_data.get("thumbnail_url"),
                    source=video_data.get("source", "YouTube"),
                    category=video_data.get("category", "Technology"),
                )
                
                self.video_repo.create(video)
                saved_count += 1
                logger.info(f"Saved video: {video.title}")
                
            except Exception as e:
                logger.error(f"Error saving video {video_data.get('title')}: {str(e)}")
        
        return saved_count
