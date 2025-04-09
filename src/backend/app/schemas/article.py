
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class TagBase(BaseModel):
    name: str

class Tag(TagBase):
    id: int
    
    class Config:
        orm_mode = True

class ArticleBase(BaseModel):
    title: str
    source_url: str
    content: str
    summary: str
    image_url: Optional[str] = None

class ArticleCreate(ArticleBase):
    pass

class Article(ArticleBase):
    id: int
    created_at: datetime
    tags: List[str] = []
    
    class Config:
        orm_mode = True

class ArticleWithConversation(Article):
    conversations: List['ConversationOut'] = []
    
    class Config:
        orm_mode = True

# To avoid circular import
from app.schemas.conversation import ConversationOut
ArticleWithConversation.update_forward_refs()
