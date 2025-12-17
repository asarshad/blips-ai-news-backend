"""AI Chat routes for the REST API."""

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from typing import Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.core.exceptions import (
    ArticleNotFoundError, 
    ChatGenerationError,
    not_found_exception,
    quota_exceeded_exception,
    internal_error_exception,
)
from app.schemas.conversation import ConversationCreate
from app.services.ai_chat import AiChatService
from app.services.quota_manager import QuotaManager
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.usage_repo import UsageRepository

router = APIRouter()


def _get_device_id(request: Request, user_agent: Optional[str]) -> str:
    """Generate a device ID from client IP and user agent."""
    client_ip = request.client.host
    return f"{client_ip}_{user_agent[:50]}" if user_agent else client_ip


@router.post("/respond")
def get_ai_response(
    request: Request,
    message: ConversationCreate,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None)
):
    """Generate AI response for a message about an article."""
    # Create repositories
    article_repo = ArticleRepository(db)
    conversation_repo = ConversationRepository(db)
    usage_repo = UsageRepository(db)
    
    device_id = _get_device_id(request, user_agent)
    
    # Check quota
    quota_manager = QuotaManager(usage_repo, redis_client)
    quota = quota_manager.check_quota(device_id, message.article_id)
    
    if quota["remaining_daily_messages"] <= 0:
        raise quota_exceeded_exception("daily")
    
    if quota["remaining_article_messages"] is not None and quota["remaining_article_messages"] <= 0:
        raise quota_exceeded_exception("article")
    
    # Get AI response
    ai_service = AiChatService(article_repo, conversation_repo)
    
    try:
        response = ai_service.get_ai_response(message.article_id, message.message)
    except ArticleNotFoundError:
        raise not_found_exception("Article", message.article_id)
    except ChatGenerationError as e:
        raise internal_error_exception(f"Failed to generate response: {e.message}")
    
    # Save conversation
    ai_service.save_conversation(
        message.article_id,
        message.message,
        response["response"]
    )
    
    # Update usage
    quota_manager.update_usage(
        device_id,
        message.article_id,
        response.get("tokens_used", 0)
    )
    
    return {
        "response": response["response"],
        "remaining_daily": quota["remaining_daily_messages"] - 1,
        "remaining_article": (quota["remaining_article_messages"] - 1) 
            if quota["remaining_article_messages"] is not None else None
    }
