
from pydantic import BaseModel
from datetime import datetime

class UsageBase(BaseModel):
    device_id: str
    article_id: int = None
    used_tokens: int = 0
    message_count: int = 0

class UsageCreate(UsageBase):
    pass

class Usage(UsageBase):
    id: int
    timestamp: datetime
    
    model_config = {
        "from_attributes": True
    }

class UsageStats(BaseModel):
    remaining_daily_messages: int
    remaining_article_messages: int = None
