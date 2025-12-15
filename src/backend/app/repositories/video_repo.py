"""
Video repository for database operations on videos.
"""

from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.repositories.base import BaseRepository
from app.models.video import Video


class VideoRepository(BaseRepository[Video]):
    """Repository for Video model database operations."""
    
    def __init__(self, db: Session):
        super().__init__(db, Video)
    
    def get_by_url(self, video_url: str) -> Optional[Video]:
        """Get video by URL."""
        return self.db.query(Video).filter(Video.video_url == video_url).first()
    
    def get_by_video_id(self, video_id: str) -> Optional[Video]:
        """Get video by video_id field."""
        return self.db.query(Video).filter(Video.video_id == video_id).first()
    
    def exists_by_url(self, video_url: str) -> bool:
        """Check if video exists by URL."""
        return self.get_by_url(video_url) is not None
    
    def exists_by_video_id(self, video_id: str) -> bool:
        """Check if video exists by video_id."""
        return self.get_by_video_id(video_id) is not None
    
    def get_recent(self, limit: int = 10) -> List[Video]:
        """Get most recent videos."""
        return self.db.query(Video).order_by(
            desc(Video.created_at)
        ).limit(limit).all()
    
    def get_published_since(self, since: datetime, limit: int = 50) -> List[Video]:
        """Get videos published since a given date."""
        return self.db.query(Video).filter(
            Video.published_at >= since
        ).order_by(
            desc(Video.published_at)
        ).limit(limit).all()
    
    def get_most_recent(self) -> Optional[Video]:
        """Get the most recently created video."""
        return self.db.query(Video).order_by(desc(Video.created_at)).first()
    
    def create_from_data(
        self,
        video_id: str,
        title: str,
        description: str,
        video_url: str,
        thumbnail_url: str,
        channel_title: str,
        published_at: datetime,
        summary: Optional[str] = None
    ) -> Video:
        """Create a video from individual fields."""
        video = Video(
            video_id=video_id,
            title=title,
            description=description,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            channel_title=channel_title,
            published_at=published_at,
            summary=summary or "Summary unavailable at the moment."
        )
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video
    
    def get_with_placeholder_summary(self, limit: int = 10) -> List[Video]:
        """Get videos that have placeholder summaries."""
        return self.db.query(Video).filter(
            Video.summary == "Summary unavailable at the moment."
        ).limit(limit).all()
    
    def update_summary(self, video_id: int, summary: str) -> Optional[Video]:
        """Update video summary."""
        video = self.get_by_id(video_id)
        if video:
            video.summary = summary
            self.db.commit()
            self.db.refresh(video)
        return video
