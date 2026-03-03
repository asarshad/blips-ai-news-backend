"""AI Chat routes for the REST API."""

from typing import Optional

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.config import settings as _settings
from app.core.dependencies import get_db, get_redis
from app.core.device_id import get_device_id as _get_device_id_hashed
from app.core.exceptions import (
    ArticleNotFoundError,
    ChatGenerationError,
    internal_error_exception,
    not_found_exception,
    quota_exceeded_exception,
)
from app.core.feature_flags import FeatureFlags, get_feature_flags
from app.repositories.content_repo import ContentItemRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.usage_repo import UsageRepository
from app.schemas.conversation import ConversationCreate
from app.services.ai_chat import AiChatService
from app.services.quota_manager import QuotaManager

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter()


def _get_device_id(request: Request, user_agent: Optional[str]) -> str:
    """Generate a hashed device ID from client IP and user agent."""
    return _get_device_id_hashed(request, user_agent)


@router.post("/respond")
@_limiter.limit(_settings.RATE_LIMIT_CHAT)
def get_ai_response(
    request: Request,
    message: ConversationCreate,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    flags: FeatureFlags = Depends(get_feature_flags),
    user_agent: Optional[str] = Header(None),
):
    """Generate AI response for a message about a content item."""
    # Check chat feature flag
    if not flags.is_enabled("chat"):
        raise HTTPException(status_code=503, detail="AI chat feature is currently disabled")

    # Create repositories
    content_repo = ContentItemRepository(db)
    conversation_repo = ConversationRepository(db)
    usage_repo = UsageRepository(db)

    device_id = _get_device_id(request, user_agent)

    # Check quota
    quota_manager = QuotaManager(usage_repo, redis_client)

    content_id = message.content_item_id
    quota = quota_manager.check_quota(device_id, content_id)

    if quota["remaining_daily_messages"] <= 0:
        raise quota_exceeded_exception("daily")

    if quota["remaining_article_messages"] is not None and quota["remaining_article_messages"] <= 0:
        raise quota_exceeded_exception("article")

    # Get AI response
    ai_service = AiChatService(content_repo, conversation_repo)

    # Prepare history if provided
    history_dicts = None
    if message.history is not None:
        history_dicts = [
            {"sender": "user" if msg.role == "user" else "ai", "message": msg.content}
            for msg in message.history
        ]

    try:
        response = ai_service.get_ai_response(
            content_item_id=message.content_item_id,
            user_message=message.message,
            history=history_dicts,
        )
    except ArticleNotFoundError:
        raise not_found_exception("Content item", content_id) from None
    except ChatGenerationError as e:
        raise internal_error_exception(f"Failed to generate response: {e.message}") from e

    # Note: Conversation persistence removed per requirement to keep chats device-only.
    # Usage tracking is still preserved below.

    # Update usage
    quota_manager.update_usage(device_id, message.content_item_id, response.get("tokens_used", 0))

    return {
        "response": response["response"],
        "remaining_daily": quota["remaining_daily_messages"] - 1,
        "remaining_article": (quota["remaining_article_messages"] - 1)
        if quota["remaining_article_messages"] is not None
        else None,
    }
