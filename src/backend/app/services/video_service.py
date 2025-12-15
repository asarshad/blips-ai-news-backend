
from typing import List, Optional
import random
from itertools import zip_longest

from app.core.logging import get_logger
from app.models.video import Video
from app.schemas.video import VideoCreate
from app.repositories.video_repo import VideoRepository

logger = get_logger(__name__)


class VideoService:
    def __init__(self, video_repo: VideoRepository):
        self.video_repo = video_repo

    def get_recent_videos(self, limit: int = 10) -> List[Video]:
        """Get most recent videos, interleaved by source for variety"""
        try:
            # Get all recent videos
            all_videos = self.video_repo.get_recent(limit * 2)
            
            # Group videos by source (channel)
            videos_by_source = {}
            for video in all_videos:
                source = video.source or "Unknown"
                if source not in videos_by_source:
                    videos_by_source[source] = []
                videos_by_source[source].append(video)
            
            # Interleave videos from different sources
            source_lists = list(videos_by_source.values())
            random.shuffle(source_lists)  # Randomize source order
            
            interleaved = []
            for videos_tuple in zip_longest(*source_lists):
                for video in videos_tuple:
                    if video is not None:
                        interleaved.append(video)
            
            return interleaved[:limit]
        except Exception as e:
            logger.error(f"Error getting recent videos: {str(e)}")
            return []

    def get_video_by_id(self, video_id: int) -> Optional[Video]:
        """Get a video by ID"""
        try:
            return self.video_repo.get_by_id(video_id)
        except Exception as e:
            logger.error(f"Error getting video by id: {str(e)}")
            return None

    def create_video(self, video_data: VideoCreate) -> Video:
        """Create a new video"""
        try:
            db_video = Video(**video_data.model_dump())
            return self.video_repo.create(db_video)
        except Exception as e:
            logger.error(f"Error creating video: {str(e)}")
            raise

    def video_exists(self, video_url: str) -> bool:
        """Check if a video already exists by URL"""
        try:
            return self.video_repo.get_by_url(video_url) is not None
        except Exception as e:
            logger.error(f"Error checking video existence: {str(e)}")
            return False
