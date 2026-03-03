from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ConversationBase(BaseModel):
    message: str
    sender: str  # "user" or "ai"


class ConversationCreate(ConversationBase):
    content_item_id: int
    history: Optional[List[ChatMessage]] = None


class ConversationOut(ConversationBase):
    id: int
    content_item_id: int
    timestamp: datetime
    model_config = {"from_attributes": True}


class Conversation(ConversationOut):
    """Alias for ConversationOut to make imports more intuitive"""

    pass


class ConversationHistory(BaseModel):
    content_item_id: int
    conversations: List[ConversationOut]
