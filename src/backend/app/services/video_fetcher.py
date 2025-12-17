
"""Video fetcher service for retrieving videos from YouTube channels."""

from typing import List, Dict, Any, Optional

from app.core.logging import get_logger
from app.repositories.video_repo import VideoRepository
from app.integrations.youtube_client import YouTubeClient, VideoEntry

logger = get_logger(__name__)


class VideoFetcher:
    """Service for fetching videos from YouTube channels."""
    
    def __init__(
        self, 
        video_repo: VideoRepository,
        youtube_client: Optional[YouTubeClient] = None
    ):
        self.video_repo = video_repo
        self.youtube_client = youtube_client or YouTubeClient()

    def fetch_latest_videos(self) -> List[Dict[str, Any]]:
        """
        Fetch latest videos from YouTube channels, filtering out existing ones.
        
        Returns:
            List of video data dictionaries for new videos only
        """
        videos = []
        video_entries = self.youtube_client.fetch_all_channels(videos_per_channel=5)
        
        for entry in video_entries:
            if self.video_repo.get_by_url(entry.video_url):
                logger.debug(f"Video already exists: {entry.title}")
                continue
            
            video_data = self._entry_to_video_data(entry)
            videos.append(video_data)
            logger.info(f"Added new video: {entry.title}")
        
        logger.info(f"Total new videos: {len(videos)}")
        return videos
    
    def _entry_to_video_data(self, entry: VideoEntry) -> Dict[str, Any]:
        """Convert a VideoEntry to video data dictionary."""
        return {
            "title": entry.title,
            "summary": entry.summary,
            "video_url": entry.video_url,
            "source_url": entry.video_url,
            "thumbnail_url": entry.thumbnail_url,
            "source": entry.source,
            "category": entry.category,
        }

    def save_videos(self, videos: List[Dict[str, Any]]) -> int:
        """
        Save fetched videos to database.
        
        Args:
            videos: List of video data dictionaries
            
        Returns:
            Number of videos successfully saved
        """
        saved_count = 0
        
        for video_data in videos:
            try:
                # Double-check existence to handle race conditions
                if self.video_repo.get_by_url(video_data["video_url"]):
                    continue
                
                db_video_data = {
                    "title": video_data["title"],
                    "summary": video_data["summary"],
                    "video_url": video_data["video_url"],
                    "source_url": video_data["source_url"],
                    "thumbnail_url": video_data.get("thumbnail_url"),
                    "source": video_data.get("source", "YouTube"),
                    "category": video_data.get("category", "Technology"),
                }
                
                self.video_repo.create(db_video_data)
                saved_count += 1
                logger.info(f"Saved video: {video_data['title']}")
                
            except Exception as e:
                # Rollback the failed transaction so subsequent saves can work
                self.video_repo.db.rollback()
                logger.error(f"Error saving video '{video_data.get('title')}': {str(e)}")
        
        return saved_count
