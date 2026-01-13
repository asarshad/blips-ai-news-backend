"""
Conversation repository for database operations on conversations.

Note: The Conversation model represents individual messages in a conversation,
not a conversation container. Each row is a message with content_item_id linking
all messages in a conversation thread.
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.repositories.base import BaseRepository
from app.models.conversation import Conversation


class ConversationRepository(BaseRepository[Conversation]):
    """
    Repository for Conversation model database operations.
    
    The Conversation model represents individual messages. Multiple 
    Conversation rows with the same content_item_id form a conversation thread.
    """
    
    def __init__(self, db: Session):
        super().__init__(db, Conversation)
    
    def get_content_messages(
        self, 
        content_item_id: int
    ) -> List[Conversation]:
        """Get all messages for a content item conversation, ordered by timestamp."""
        return self.db.query(Conversation).filter(
            Conversation.content_item_id == content_item_id
        ).order_by(Conversation.timestamp).all()
    
    def get_recent_messages(
        self, 
        content_item_id: int, 
        limit: int = 50
    ) -> List[Conversation]:
        """Get most recent messages for a content item."""
        return self.db.query(Conversation).filter(
            Conversation.content_item_id == content_item_id
        ).order_by(
            desc(Conversation.timestamp)
        ).limit(limit).all()[::-1]  # Reverse to get chronological order
    
    def add_message(
        self, 
        content_item_id: int, 
        sender: str, 
        message: str
    ) -> Conversation:
        """Add a new message to a conversation."""
        conversation = Conversation(
            content_item_id=content_item_id,
            sender=sender,
            message=message
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation
    
    def add_user_message(self, content_item_id: int, message: str) -> Conversation:
        """Add a user message to a conversation."""
        return self.add_message(content_item_id, "user", message)
    
    def add_ai_message(self, content_item_id: int, message: str) -> Conversation:
        """Add an AI message to a conversation."""
        return self.add_message(content_item_id, "ai", message)
    
    def clear_conversation(self, content_item_id: int) -> int:
        """Delete all messages for a content item. Returns number deleted."""
        count = self.db.query(Conversation).filter(
            Conversation.content_item_id == content_item_id
        ).delete()
        self.db.commit()
        return count
    
    def get_content_items_with_conversations(self) -> List[int]:
        """Get list of content item IDs that have conversations."""
        results = self.db.query(Conversation.content_item_id).distinct().all()
        return [r[0] for r in results if r[0] is not None]

