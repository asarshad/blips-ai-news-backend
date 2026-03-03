"""Conversation routes for the REST API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.repositories.content_repo import ContentItemRepository
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.conversation import ConversationHistory, ConversationOut

router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


def get_conversation_repo(db: Session = Depends(get_db)) -> ConversationRepository:
    """Factory for ConversationRepository."""
    return ConversationRepository(db)


@router.get("/{content_item_id}", response_model=ConversationHistory)
def get_conversations(
    content_item_id: int,
    content_repo: ContentItemRepository = Depends(get_content_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Get conversation history for a content item."""
    content_item = content_repo.get_by_id(content_item_id)
    if not content_item:
        raise not_found_exception("Content item", content_item_id)

    conversations = conversation_repo.get_content_messages(content_item_id)
    return {"content_item_id": content_item_id, "conversations": conversations}


@router.post("/{content_item_id}", response_model=ConversationOut)
def save_message(
    content_item_id: int,
    message: str,
    sender: str,
    content_repo: ContentItemRepository = Depends(get_content_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Save a new message to the conversation history."""
    content_item = content_repo.get_by_id(content_item_id)
    if not content_item:
        raise not_found_exception("Content item", content_item_id)

    if sender not in ("user", "ai"):
        raise HTTPException(status_code=400, detail="Invalid sender. Must be 'user' or 'ai'")

    return conversation_repo.add_message(content_item_id, sender, message)


@router.delete("/{content_item_id}", status_code=204)
def delete_conversation(
    content_item_id: int,
    content_repo: ContentItemRepository = Depends(get_content_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Delete conversation history for a content item."""
    content_item = content_repo.get_by_id(content_item_id)
    if not content_item:
        raise not_found_exception("Content item", content_item_id)

    conversation_repo.clear_conversation(content_item_id)
    return None
