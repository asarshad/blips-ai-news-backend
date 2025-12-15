
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.core.dependencies import get_db
from app.schemas.conversation import ConversationOut, ConversationHistory
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository

router = APIRouter()

@router.get("/{article_id}", response_model=ConversationHistory)
def get_conversations(
    article_id: int,
    db: Session = Depends(get_db)
):
    # Create repositories
    article_repo = ArticleRepository(db)
    conversation_repo = ConversationRepository(db)
    
    # Verify article exists
    article = article_repo.get_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Get conversations
    conversations = conversation_repo.get_article_messages(article_id)
    
    return {
        "article_id": article_id,
        "conversations": conversations
    }

@router.post("/{article_id}", response_model=ConversationOut)
def save_message(
    article_id: int,
    message: str,
    sender: str,
    db: Session = Depends(get_db)
):
    # Create repositories
    article_repo = ArticleRepository(db)
    conversation_repo = ConversationRepository(db)
    
    # Verify article exists
    article = article_repo.get_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Verify sender is valid
    if sender not in ["user", "ai"]:
        raise HTTPException(status_code=400, detail="Invalid sender. Must be 'user' or 'ai'")
    
    # Create new conversation message using repository
    conversation = conversation_repo.add_message(article_id, sender, message)
    
    return conversation
