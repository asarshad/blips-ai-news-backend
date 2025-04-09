
from pydantic import BaseModel
from datetime import datetime
from typing import List

class ConversationBase(BaseModel):
    message: str
    sender: str  # "user" or "ai"

class ConversationCreate(ConversationBase):
    article_id: int

class ConversationOut(ConversationBase):
    id: int
    article_id: int
    timestamp: datetime
    
    class Config:
        orm_mode = True

class ConversationHistory(BaseModel):
    article_id: int
    conversations: List[ConversationOut]
