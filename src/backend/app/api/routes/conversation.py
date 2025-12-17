"""Conversation routes for the REST API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.schemas.conversation import ConversationOut, ConversationHistory
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository

router = APIRouter()


def get_article_repo(db: Session = Depends(get_db)) -> ArticleRepository:
    """Factory for ArticleRepository."""
    return ArticleRepository(db)


def get_conversation_repo(db: Session = Depends(get_db)) -> ConversationRepository:
    """Factory for ConversationRepository."""
    return ConversationRepository(db)


@router.get("/{article_id}", response_model=ConversationHistory)
def get_conversations(
    article_id: int,
    article_repo: ArticleRepository = Depends(get_article_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo)
):
    """Get conversation history for an article."""
    article = article_repo.get_by_id(article_id)
    if not article:
        raise not_found_exception("Article", article_id)
    
    conversations = conversation_repo.get_article_messages(article_id)
    return {"article_id": article_id, "conversations": conversations}


@router.post("/{article_id}", response_model=ConversationOut)
def save_message(
    article_id: int,
    message: str,
    sender: str,
    article_repo: ArticleRepository = Depends(get_article_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo)
):
    """Save a new message to the conversation history."""
    article = article_repo.get_by_id(article_id)
    if not article:
        raise not_found_exception("Article", article_id)
    
    if sender not in ("user", "ai"):
        raise HTTPException(status_code=400, detail="Invalid sender. Must be 'user' or 'ai'")
    
    return conversation_repo.add_message(article_id, sender, message)
