
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.core.dependencies import get_db
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationOut, ConversationHistory
from app.models.article import Article

router = APIRouter()

@router.get("/{article_id}", response_model=ConversationHistory)
def get_conversations(
    article_id: int,
    db: Session = Depends(get_db)
):
    # Verify article exists
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Get conversations
    conversations = db.query(Conversation).filter(
        Conversation.article_id == article_id
    ).order_by(Conversation.timestamp).all()
    
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
    # Verify article exists
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Verify sender is valid
    if sender not in ["user", "ai"]:
        raise HTTPException(status_code=400, detail="Invalid sender. Must be 'user' or 'ai'")
    
    # Create new conversation message
    conversation = Conversation(
        article_id=article_id,
        message=message,
        sender=sender
    )
    
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    
    return conversation
