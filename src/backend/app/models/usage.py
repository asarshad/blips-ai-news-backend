
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime
from app.db.base import Base

class Usage(Base):
    __tablename__ = "usage"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, index=True)  # IP address or token
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True)
    used_tokens = Column(Integer, default=0)
    message_count = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.utcnow)
