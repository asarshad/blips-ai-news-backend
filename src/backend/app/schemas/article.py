
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.schemas.conversation import Conversation

class Tag(BaseModel):
    name: str

    class Config:
        orm_mode = True

class TagCount(BaseModel):
    name: str
    count: int

class Article(BaseModel):
    id: int
    title: str
    source_url: str
    summary: str
    image_url: Optional[str] = None
    created_at: datetime
    tags: List[Tag] = []

    class Config:
        orm_mode = True

class ArticleWithConversation(Article):
    conversations: List[Conversation] = []

    class Config:
        orm_mode = True

class ArticleList(BaseModel):
    articles: List[Article]
