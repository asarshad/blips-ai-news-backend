
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id"))
    message = Column(Text)
    sender = Column(String)  # "user" or "ai"
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    content_item = relationship("ContentItem", back_populates="conversations")
