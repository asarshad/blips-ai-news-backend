
from fastapi import APIRouter, Depends, Request, Header
from sqlalchemy.orm import Session
from typing import Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager

router = APIRouter()

@router.get("", response_model=UsageStats)
def get_usage_stats(
    request: Request,
    article_id: Optional[int] = None,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None)
):
    # Get client IP for tracking
    client_ip = request.client.host
    device_id = f"{client_ip}_{user_agent[:50]}" if user_agent else client_ip
    
    # Get quota information
    quota_manager = QuotaManager(db, redis_client)
    quota = quota_manager.check_quota(device_id, article_id)
    
    return quota
