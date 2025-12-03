
from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from app.db.base import Base


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    summary = Column(Text)
    video_url = Column(String, unique=True, index=True)
    source_url = Column(String)
    thumbnail_url = Column(String, nullable=True)
    source = Column(String, default="YouTube")
    category = Column(String, default="Technology")
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
