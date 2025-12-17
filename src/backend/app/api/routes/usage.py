"""Usage quota routes for the REST API."""

from fastapi import APIRouter, Depends, Request, Header
from sqlalchemy.orm import Session
from typing import Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager
from app.repositories.usage_repo import UsageRepository

router = APIRouter()


def _get_device_id(request: Request, user_agent: Optional[str]) -> str:
    """Generate a device ID from client IP and user agent."""
    client_ip = request.client.host
    return f"{client_ip}_{user_agent[:50]}" if user_agent else client_ip


@router.get("", response_model=UsageStats)
def get_usage_stats(
    request: Request,
    article_id: Optional[int] = None,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None)
):
    """Get current quota usage statistics for the device."""
    usage_repo = UsageRepository(db)
    device_id = _get_device_id(request, user_agent)
    
    quota_manager = QuotaManager(usage_repo, redis_client)
    return quota_manager.check_quota(device_id, article_id)
