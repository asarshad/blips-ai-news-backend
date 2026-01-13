
from app.core.logging import get_logger
from app.core.exceptions import ArticleNotFoundError, ChatGenerationError
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.video_repo import VideoRepository
from app.integrations.llm_client import LLMClient
from typing import Dict, Any, Optional, List

logger = get_logger(__name__)


class AiChatService:
    """Service for AI-powered chat about articles."""
    
    def __init__(
        self, 
        article_repo: ArticleRepository, 
        conversation_repo: ConversationRepository,
        video_repo: Optional[VideoRepository] = None,
        llm_client: Optional[LLMClient] = None
    ):
        self.article_repo = article_repo
        self.conversation_repo = conversation_repo
        self.video_repo = video_repo
        self.llm_client = llm_client or LLMClient()
    
    def get_ai_response(
        self, 
        article_id: Optional[int] = None, 
        user_message: str = "", 
        history_limit: int = 3,
        history: Optional[List[Dict[str, str]]] = None,
        video_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate AI response to user message with article context.
        
        Args:
            article_id: ID of the article being discussed
            user_message: User's message/question
            history_limit: Number of recent messages to include for context
            history: Optional list of previous messages. If provided, DB lookup is skipped.
            video_id: ID of the video being discussed (optional)
            
        Returns:
            Dict with 'response' and 'tokens_used' keys
            
        Raises:
            ArticleNotFoundError: If article doesn't exist
            ChatGenerationError: If AI response generation fails
        """
        title = ""
        summary = ""
        
        # Get content context
        if article_id:
            article = self.article_repo.get_by_id(article_id)
            if not article:
                raise ArticleNotFoundError(article_id)
            title = article.title
            summary = article.summary or ""
        elif video_id and self.video_repo:
            video = self.video_repo.get_by_id(video_id)
            if not video:
                # Reusing ArticleNotFoundError for simplicity as it maps to 404
                raise ArticleNotFoundError(video_id)
            title = video.title
            summary = video.summary or ""
        else:
            raise ValueError("Either article_id or video_id must be provided")
        
        try:
            # Get recent conversation history
            if history is not None:
                history_dicts = history
            else:
                # Fallback to DB history if not provided (only for articles for now)
                if article_id:
                    db_history = self.conversation_repo.get_recent_by_article(article_id, history_limit)
                    history_dicts = [
                        {"sender": msg.sender, "message": msg.message}
                        for msg in db_history
                    ]
                else:
                    history_dicts = []
            
            # Use LLM client for chat
            response = self.llm_client.generate_chat_response(
                article_title=title,
                article_summary=summary,
                conversation_history=history_dicts,
                user_message=user_message
            )
            
            return {
                "response": response.content,
                "tokens_used": response.tokens_used
            }
            
        except Exception as e:
            logger.error(f"Error generating AI response: {str(e)}")
            raise ChatGenerationError(
                "Failed to generate AI response",
                details=str(e)
            )
    
    def save_conversation(self, article_id: int, user_message: str, ai_response: str) -> Dict[str, Any]:
        """
        Save user message and AI response to conversation history.
        
        Args:
            article_id: ID of the article being discussed
            user_message: The user's message
            ai_response: The AI's response
            
        Returns:
            Dict with 'user_id' and 'ai_id' conversation record IDs
        """
        user_conv = self.conversation_repo.add_user_message(article_id, user_message)
        ai_conv = self.conversation_repo.add_ai_message(article_id, ai_response)
        
        return {
            "user_id": user_conv.id,
            "ai_id": ai_conv.id
        }
