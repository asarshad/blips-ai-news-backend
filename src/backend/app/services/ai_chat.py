
from app.core.logging import get_logger
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository
from app.integrations.openai_client import OpenAIClient
from typing import Dict, Any, Optional

logger = get_logger(__name__)

class AiChatService:
    """
    Service for AI-powered chat about articles.
    
    Uses OpenAIClient for chat completions and repositories for
    article and conversation data.
    """
    
    def __init__(
        self, 
        article_repo: ArticleRepository, 
        conversation_repo: ConversationRepository,
        openai_client: Optional[OpenAIClient] = None
    ):
        self.article_repo = article_repo
        self.conversation_repo = conversation_repo
        self.openai_client = openai_client or OpenAIClient()
    
    def get_ai_response(self, article_id: int, user_message: str, history_limit: int = 3) -> Dict[str, Any]:
        """Generate AI response to user message with article context"""
        try:
            # Get article
            article = self.article_repo.get_by_id(article_id)
            if not article:
                return {"error": "Article not found"}
            
            # Get recent conversation history
            history = self.conversation_repo.get_recent_by_article(article_id, history_limit)
            
            # Convert history to format expected by OpenAI client
            history_dicts = [
                {"sender": msg.sender, "message": msg.message}
                for msg in history
            ]
            
            # Use OpenAI client for chat
            response = self.openai_client.generate_chat_response(
                article_title=article.title,
                article_summary=article.summary or "",
                conversation_history=history_dicts,
                user_message=user_message
            )
            
            return {
                "response": response.content,
                "tokens_used": response.tokens_used
            }
            
        except Exception as e:
            logger.error(f"Error generating AI response: {str(e)}")
            return {
                "response": "I'm sorry, I'm having trouble processing your request right now. Please try again later.",
                "tokens_used": 0
            }
    
    def save_conversation(self, article_id: int, user_message: str, ai_response: str) -> Dict[str, Any]:
        """Save user message and AI response to conversation history"""
        try:
            user_conv = self.conversation_repo.add_user_message(article_id, user_message)
            ai_conv = self.conversation_repo.add_ai_message(article_id, ai_response)
            
            return {
                "user_id": user_conv.id,
                "ai_id": ai_conv.id
            }
            
        except Exception as e:
            logger.error(f"Error saving conversation: {str(e)}")
            raise
