
from sqlalchemy import Column, Integer, String, Text, DateTime, Date, ForeignKey, Table, func
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base

# Association table for article tags
article_tag = Table(
    'article_tag',
    Base.metadata,
    Column('article_id', Integer, ForeignKey('articles.id'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id'), primary_key=True),
)

class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    source_url = Column(String, unique=True, index=True)
    content = Column(Text)
    summary = Column(Text)
    image_url = Column(String, nullable=True)
    published_date = Column(Date, index=True)  # Actual publish date from RSS feed
    created_at = Column(DateTime, default=datetime.utcnow)  # When we added it to DB
    hot_score = Column(Integer, default=0, index=True)  # Higher score = more prominent
    read_time_minutes = Column(Integer, default=1)  # Estimated read time based on content length
    
    # Relationships
    conversations = relationship("Conversation", back_populates="article", cascade="all, delete-orphan")
    tags = relationship("Tag", secondary=article_tag, back_populates="articles")
    
class Tag(Base):
    __tablename__ = "tags"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    
    # Relationships
    articles = relationship("Article", secondary=article_tag, back_populates="tags")
