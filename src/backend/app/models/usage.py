from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.base import Base


class Usage(Base):
    __tablename__ = "usage"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, index=True)  # IP address or token
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=True)
    used_tokens = Column(Integer, default=0)
    message_count = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.utcnow)
