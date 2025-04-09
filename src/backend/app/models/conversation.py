
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"))
    message = Column(Text)
    sender = Column(String)  # "user" or "ai"
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    article = relationship("Article", back_populates="conversations")
