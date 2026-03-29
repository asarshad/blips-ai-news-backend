"""Usage quota routes for the REST API."""

from typing import Optional

import redis
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_redis
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.repositories.usage_repo import UsageRepository
from app.schemas.usage import UsageStats
from app.services.quota_manager import QuotaManager

router = APIRouter()


@router.get("", response_model=UsageStats)
def get_usage_stats(
    content_item_id: Optional[int] = None,
    session: AuthenticatedSession = Depends(require_session_token),
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
):
    """Get current quota usage statistics for the device."""
    usage_repo = UsageRepository(db)

    quota_manager = QuotaManager(usage_repo, redis_client)
    return quota_manager.check_quota(session.device_id, content_item_id)
