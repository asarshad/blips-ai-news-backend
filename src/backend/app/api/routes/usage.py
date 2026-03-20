"""Usage quota routes for the REST API."""

from typing import Optional

import redis
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_redis
from app.core.device_id import resolve_device_id
from app.repositories.usage_repo import UsageRepository
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager

router = APIRouter()


@router.get("", response_model=UsageStats)
def get_usage_stats(
    request: Request,
    content_item_id: Optional[int] = None,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None, alias="X-Device-ID"),
):
    """Get current quota usage statistics for the device."""
    usage_repo = UsageRepository(db)
    device_id = resolve_device_id(request, x_device_id=x_device_id, user_agent=user_agent)

    quota_manager = QuotaManager(usage_repo, redis_client)
    return quota_manager.check_quota(device_id, content_item_id)
