
"""Video service for business logic related to videos."""

from typing import List
import random
from itertools import zip_longest

from app.core.logging import get_logger
from app.core.exceptions import VideoNotFoundError
from app.models.video import Video
from app.schemas.video import VideoCreate
from app.repositories.video_repo import VideoRepository

logger = get_logger(__name__)


class VideoService:
    """Service for video-related business logic."""
    
    def __init__(self, video_repo: VideoRepository):
        self.video_repo = video_repo

    def get_recent_videos(self, limit: int = 10) -> List[Video]:
        """
        Get most recent videos, interleaved by source for variety.
        
        Args:
            limit: Maximum number of videos to return
            
        Returns:
            List of videos interleaved by source
        """
        all_videos = self.video_repo.get_recent(limit * 2)
        
        # Group by source for interleaving
        videos_by_source: dict[str, List[Video]] = {}
        for video in all_videos:
            source = video.source or "Unknown"
            videos_by_source.setdefault(source, []).append(video)
        
        # Interleave videos from different sources
        source_lists = list(videos_by_source.values())
        random.shuffle(source_lists)
        
        interleaved = [
            video
            for videos_tuple in zip_longest(*source_lists)
            for video in videos_tuple
            if video is not None
        ]
        
        return interleaved[:limit]

    def get_video_by_id(self, video_id: int) -> Video:
        """
        Get a video by ID.
        
        Args:
            video_id: ID of the video
            
        Returns:
            Video instance
            
        Raises:
            VideoNotFoundError: If video doesn't exist
        """
        video = self.video_repo.get_by_id(video_id)
        if not video:
            raise VideoNotFoundError(video_id)
        return video

    def create_video(self, video_data: VideoCreate) -> Video:
        """Create a new video."""
        return self.video_repo.create(video_data.model_dump())

    def video_exists(self, video_url: str) -> bool:
        """Check if a video already exists by URL."""
        return self.video_repo.get_by_url(video_url) is not None
