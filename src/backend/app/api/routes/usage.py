
from fastapi import APIRouter, Depends, Request, Header
from sqlalchemy.orm import Session
from typing import Optional
import redis

from app.db.base import get_db
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager
from app.core.config import settings

# Redis connection
redis_client = redis.from_url(settings.REDIS_URL)

router = APIRouter()

@router.get("", response_model=UsageStats)
def get_usage_stats(
    request: Request,
    article_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user_agent: Optional[str] = Header(None)
):
    # Get client IP for tracking
    client_ip = request.client.host
    device_id = f"{client_ip}_{user_agent[:50]}" if user_agent else client_ip
    
    # Get quota information
    quota_manager = QuotaManager(db, redis_client)
    quota = quota_manager.check_quota(device_id, article_id)
    
    return quota
