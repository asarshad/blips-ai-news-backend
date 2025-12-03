
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class VideoBase(BaseModel):
    title: str
    summary: str
    video_url: str
    source_url: str
    thumbnail_url: Optional[str] = None
    source: str = "YouTube"
    category: str = "Technology"
    duration_seconds: Optional[int] = None


class VideoCreate(VideoBase):
    pass


class Video(VideoBase):
    id: int
    created_at: datetime

    model_config = {
        "from_attributes": True
    }


class VideoList(BaseModel):
    videos: List[Video]
