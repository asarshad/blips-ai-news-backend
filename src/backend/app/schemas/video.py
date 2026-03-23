from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class VideoBase(BaseModel):
    title: str
    summary: str
    video_url: str
    source_url: str
    thumbnail_url: Optional[str] = None
    source: str = "YouTube"
    category: str = "Technology"
    duration_seconds: Optional[int] = None
    hot_score: Optional[int] = 0


class VideoCreate(VideoBase):
    pass


class Video(VideoBase):
    id: int
    type: str = "VIDEO"
    created_at: datetime
    published_at: Optional[datetime] = None
    freshness_tier: Optional[str] = None
    freshness_reason: Optional[str] = None
    published_age_seconds: Optional[int] = None
    added_age_seconds: Optional[int] = None
    conversation_starters: Optional[dict[str, List[str]]] = None

    model_config = {"from_attributes": True}


class VideoList(BaseModel):
    videos: List[Video]
