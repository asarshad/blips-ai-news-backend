"""Conversation Starters API routes."""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.repositories.content_repo import ContentItemRepository
from app.services.conversation_starters import get_starters_service

router = APIRouter()


class StartersResponse(BaseModel):
    """Response model for conversation starters."""
    content_id: int
    starters: List[str]
    fallback: List[str]
    
    class Config:
        json_schema_extra = {
            "example": {
                "content_id": 123,
                "starters": [
                    "What are the implications of this AI development?",
                    "Can you explain the key technical concepts?",
                    "How does this compare to similar developments?",
                ],
                "fallback": [
                    "What are the main points?",
                    "Can you summarize this for me?",
                ],
            }
        }


@router.get("/{content_id}", response_model=StartersResponse)
def get_starters(
    content_id: int,
    regenerate: bool = False,
    db: Session = Depends(get_db),
):
    """
    Get conversation starters for a content item.
    
    Args:
        content_id: ID of the content item
        regenerate: If True, regenerate starters even if cached
        
    Returns:
        StartersResponse with starters and fallback questions
    """
    content_repo = ContentItemRepository(db)
    content_item = content_repo.get_by_id(content_id)
    
    if not content_item:
        raise not_found_exception("Content item", content_id)
    
    # Get or generate starters
    starters_service = get_starters_service()
    
    if regenerate or not content_item.conversation_starters:
        starters = starters_service.generate_and_persist(
            content_item,
            force_regenerate=regenerate
        )
        db.commit()
    else:
        starters = content_item.conversation_starters
    
    return StartersResponse(
        content_id=content_id,
        starters=starters.get("starters", []),
        fallback=starters.get("fallback", []),
    )


@router.post("/{content_id}/generate", response_model=StartersResponse)
def generate_starters(
    content_id: int,
    db: Session = Depends(get_db),
):
    """
    Force regenerate conversation starters for a content item.
    
    Use this endpoint to explicitly regenerate starters (e.g., after content update).
    """
    content_repo = ContentItemRepository(db)
    content_item = content_repo.get_by_id(content_id)
    
    if not content_item:
        raise not_found_exception("Content item", content_id)
    
    starters_service = get_starters_service()
    starters = starters_service.generate_and_persist(
        content_item,
        force_regenerate=True
    )
    db.commit()
    
    return StartersResponse(
        content_id=content_id,
        starters=starters.get("starters", []),
        fallback=starters.get("fallback", []),
    )
