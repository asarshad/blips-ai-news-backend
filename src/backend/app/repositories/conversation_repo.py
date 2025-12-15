"""
Conversation repository for database operations on conversations.

Note: The Conversation model represents individual messages in a conversation,
not a conversation container. Each row is a message with article_id linking
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
    Conversation rows with the same article_id form a conversation thread.
    """
    
    def __init__(self, db: Session):
        super().__init__(db, Conversation)
    
    def get_article_messages(
        self, 
        article_id: int
    ) -> List[Conversation]:
        """Get all messages for an article conversation, ordered by timestamp."""
        return self.db.query(Conversation).filter(
            Conversation.article_id == article_id
        ).order_by(Conversation.timestamp).all()
    
    def get_recent_messages(
        self, 
        article_id: int, 
        limit: int = 50
    ) -> List[Conversation]:
        """Get most recent messages for an article."""
        return self.db.query(Conversation).filter(
            Conversation.article_id == article_id
        ).order_by(
            desc(Conversation.timestamp)
        ).limit(limit).all()[::-1]  # Reverse to get chronological order
    
    def get_recent_by_article(
        self, 
        article_id: int, 
        limit: int = 50
    ) -> List[Conversation]:
        """
        Get most recent messages for an article in chronological order.
        Alias for get_recent_messages for API compatibility.
        """
        return self.get_recent_messages(article_id, limit)
    
    def add_message(
        self, 
        article_id: int, 
        sender: str, 
        message: str
    ) -> Conversation:
        """Add a new message to a conversation."""
        conversation = Conversation(
            article_id=article_id,
            sender=sender,
            message=message
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation
    
    def add_user_message(self, article_id: int, message: str) -> Conversation:
        """Add a user message to a conversation."""
        return self.add_message(article_id, "user", message)
    
    def add_ai_message(self, article_id: int, message: str) -> Conversation:
        """Add an AI message to a conversation."""
        return self.add_message(article_id, "ai", message)
    
    def clear_conversation(self, article_id: int) -> int:
        """Delete all messages for an article. Returns number deleted."""
        count = self.db.query(Conversation).filter(
            Conversation.article_id == article_id
        ).delete()
        self.db.commit()
        return count
    
    def get_articles_with_conversations(self) -> List[int]:
        """Get list of article IDs that have conversations."""
        results = self.db.query(Conversation.article_id).distinct().all()
        return [r[0] for r in results if r[0] is not None]

