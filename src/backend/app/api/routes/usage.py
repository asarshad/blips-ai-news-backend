"""Usage quota routes for the REST API."""

from typing import Optional

import redis
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_redis
from app.core.device_id import get_device_id as _get_device_id_hashed
from app.repositories.usage_repo import UsageRepository
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager

router = APIRouter()


def _get_device_id(request: Request, user_agent: Optional[str]) -> str:
    """Generate a hashed device ID from client IP and user agent."""
    return _get_device_id_hashed(request, user_agent)


@router.get("", response_model=UsageStats)
def get_usage_stats(
    request: Request,
    content_item_id: Optional[int] = None,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None),
):
    """Get current quota usage statistics for the device."""
    usage_repo = UsageRepository(db)
    device_id = _get_device_id(request, user_agent)

    quota_manager = QuotaManager(usage_repo, redis_client)
    return quota_manager.check_quota(device_id, content_item_id)
