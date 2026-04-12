"""AI Chat routes for the REST API."""

from typing import Optional

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.config import settings as _settings
from app.core.dependencies import get_db, get_redis
from app.core.exceptions import (
    ArticleNotFoundError,
    ChatGenerationError,
    internal_error_exception,
    not_found_exception,
    quota_exceeded_payload,
)
from app.core.feature_flags import FeatureFlags, get_feature_flags
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.repositories.content_repo import ContentItemRepository
from app.repositories.usage_repo import UsageRepository
from app.schemas.conversation import ConversationCreate
from app.services.ai_chat import AiChatService
from app.services.conversation_starters import (
    extract_exact_starters,
    get_starters_service,
    normalize_starter_prompt,
)
from app.services.quota_manager import QuotaManager
from app.core.logging import get_logger

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter()
logger = get_logger(__name__)


@router.post("/respond")
@_limiter.limit(_settings.RATE_LIMIT_CHAT)
def get_ai_response(
    request: Request,
    message: ConversationCreate,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    flags: FeatureFlags = Depends(get_feature_flags),
    session: AuthenticatedSession = Depends(require_session_token),
):
    """Generate AI response for a message about a content item."""
    # Check chat feature flag
    if not flags.is_enabled("chat"):
        raise HTTPException(status_code=503, detail="AI chat feature is currently disabled")

    # Create repositories
    content_repo = ContentItemRepository(db)
    usage_repo = UsageRepository(db)

    content_id = message.content_item_id
    content_item = content_repo.get_by_id(content_id)
    if not content_item:
        raise not_found_exception("Content item", content_id)

    # Check quota
    quota_manager = QuotaManager(usage_repo, redis_client)

    # Get AI response
    ai_service = AiChatService(content_repo)

    # Prepare history if provided
    history_dicts = None
    if message.history is not None:
        history_dicts = [
            {"sender": "user" if msg.role == "user" else "ai", "message": msg.content}
            for msg in message.history
        ]
    history_roles = [entry["sender"] for entry in history_dicts or []]

    logger.info(
        "AI chat request content_id=%s history_count=%s history_roles=%s has_previous_response_id=%s starter_prompt=%s",
        content_id,
        len(history_dicts or []),
        history_roles,
        bool(message.previous_response_id),
        bool(message.starter_prompt),
    )

    starters_service = get_starters_service(ai_service.llm_client)
    matched_starter_prompt = None
    quota = None

    if message.starter_prompt and not history_dicts:
        normalized_prompt = normalize_starter_prompt(message.message)
        if normalized_prompt in extract_exact_starters(content_item):
            matched_starter_prompt = normalized_prompt
            cached_answer = starters_service.get_cached_starter_answer(
                content_item,
                normalized_prompt,
            )
            if cached_answer:
                quota = quota_manager.check_quota(session.device_id, content_id)
                logger.info(
                    "AI chat served cached starter response content_id=%s prompt=%s",
                    content_id,
                    normalized_prompt,
                )
                return {
                    "response": cached_answer,
                    "response_id": None,
                    "remaining_daily": quota["remaining_daily_messages"],
                    "remaining_article": quota["remaining_article_messages"],
                    "used_cached_starter_response": True,
                }

    quota = quota or quota_manager.check_quota(session.device_id, content_id)

    if quota["remaining_daily_messages"] <= 0:
        return JSONResponse(status_code=429, content=quota_exceeded_payload("daily"))

    if quota["remaining_article_messages"] is not None and quota["remaining_article_messages"] <= 0:
        return JSONResponse(status_code=429, content=quota_exceeded_payload("article"))

    try:
        response = ai_service.get_ai_response(
            content_item_id=message.content_item_id,
            user_message=message.message,
            history=history_dicts,
            previous_response_id=message.previous_response_id,
        )
    except ArticleNotFoundError:
        raise not_found_exception("Content item", content_id) from None
    except ChatGenerationError as e:
        logger.error(
            "AI chat generation failed content_id=%s history_count=%s has_previous_response_id=%s details=%s",
            content_id,
            len(history_dicts or []),
            bool(message.previous_response_id),
            e.details,
        )
        raise internal_error_exception(f"Failed to generate response: {e.message}") from e

    if matched_starter_prompt:
        try:
            if starters_service.persist_starter_answer(
                content_item,
                matched_starter_prompt,
                response["response"],
            ):
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Failed to persist starter answer content_id=%s prompt=%s error=%s",
                content_id,
                matched_starter_prompt,
                exc,
            )

    # Note: Conversation persistence removed per requirement to keep chats device-only.
    # Usage tracking is still preserved below.

    # Update usage
    quota_manager.update_usage(
        session.device_id,
        message.content_item_id,
        response.get("tokens_used", 0),
    )

    return {
        "response": response["response"],
        "response_id": response.get("response_id"),
        "remaining_daily": quota["remaining_daily_messages"] - 1,
        "remaining_article": (quota["remaining_article_messages"] - 1)
        if quota["remaining_article_messages"] is not None
        else None,
        "used_cached_starter_response": False,
    }
