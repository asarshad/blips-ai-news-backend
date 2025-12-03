
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from typing import Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.models.article import Article
from app.schemas.conversation import ConversationCreate
from app.services.ai_chat import AiChatService
from app.services.quota_manager import QuotaManager

router = APIRouter()

@router.post("/respond")
def get_ai_response(
    request: Request,
    message: ConversationCreate,
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
    user_agent: Optional[str] = Header(None)
):
    # Get client IP for usage tracking
    client_ip = request.client.host
    device_id = f"{client_ip}_{user_agent[:50]}" if user_agent else client_ip
    
    # Check quota
    quota_manager = QuotaManager(db, redis_client)
    quota = quota_manager.check_quota(device_id, message.article_id)
    
    if quota["remaining_daily_messages"] <= 0:
        raise HTTPException(status_code=429, detail="Daily message quota exceeded")
    
    if quota["remaining_article_messages"] is not None and quota["remaining_article_messages"] <= 0:
        raise HTTPException(status_code=429, detail="Article message quota exceeded")
    
    # Verify article exists
    article = db.query(Article).filter(Article.id == message.article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Get AI response
    ai_service = AiChatService(db)
    response = ai_service.get_ai_response(message.article_id, message.message)
    
    if "error" in response:
        raise HTTPException(status_code=500, detail=response["error"])
    
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
