
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

class ChatMessage(BaseModel):
    role: str
    content: str

class ConversationBase(BaseModel):
    message: str
    sender: str  # "user" or "ai"

class ConversationCreate(ConversationBase):
    article_id: Optional[int] = None
    video_id: Optional[int] = None
    history: Optional[List[ChatMessage]] = None

class ConversationOut(ConversationBase):
    id: int
    article_id: int
    timestamp: datetime
    model_config = {
        "from_attributes": True
    }

class Conversation(ConversationOut):
    """Alias for ConversationOut to make imports more intuitive"""
    pass

class ConversationHistory(BaseModel):
    article_id: int
    conversations: List[ConversationOut]
