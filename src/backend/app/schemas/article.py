
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

class Tag(BaseModel):
    name: str

    model_config = {
        "from_attributes": True
    }

class TagCount(BaseModel):
    name: str
    count: int

class ArticleBase(BaseModel):
    title: str
    source_url: str
    summary: str
    image_url: Optional[str] = None

class ArticleCreate(ArticleBase):
    tags: List[str] = []

class Article(ArticleBase):
    id: int
    created_at: datetime
    tags: List[Tag] = []

    model_config = {
        "from_attributes": True
    }

# Forward reference for conversations to avoid circular imports
class ArticleWithConversation(Article):
    conversations: List["ConversationOut"] = []

    model_config = {
        "from_attributes": True
    }

class ArticleList(BaseModel):
    articles: List[Article]

# Import at the end to resolve circular import
from app.schemas.conversation import ConversationOut

# Update forward references
ArticleWithConversation.model_rebuild()
