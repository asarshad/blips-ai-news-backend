from typing import Any, Dict, List, Optional

from app.core.exceptions import ArticleNotFoundError, ChatGenerationError
from app.core.logging import get_logger
from app.integrations.llm_client import LLMClient
from app.repositories.content_repo import ContentItemRepository
from app.repositories.conversation_repo import ConversationRepository

logger = get_logger(__name__)


class AiChatService:
    """Service for AI-powered chat about content items."""

    def __init__(
        self,
        content_repo: ContentItemRepository,
        conversation_repo: ConversationRepository,
        llm_client: Optional[LLMClient] = None,
    ):
        self.content_repo = content_repo
        self.conversation_repo = conversation_repo
        self.llm_client = llm_client or LLMClient()

    def get_ai_response(
        self,
        content_item_id: int,
        user_message: str = "",
        history_limit: int = 3,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate AI response to user message with content context.

        Args:
            content_item_id: ID of the content item being discussed
            user_message: User's message/question
            history_limit: Number of recent messages to include for context
            history: Optional list of previous messages. If provided, DB lookup is skipped.

        Returns:
            Dict with 'response' and 'tokens_used' keys

        Raises:
            ArticleNotFoundError: If content item doesn't exist
            ChatGenerationError: If AI response generation fails
        """
        # Get content context
        content_item = self.content_repo.get_by_id(content_item_id)
        if not content_item:
            raise ArticleNotFoundError(content_item_id)

        title = content_item.title
        summary = content_item.summary or ""

        try:
            # Get recent conversation history
            if history is not None:
                history_dicts = history
            else:
                # Fallback to DB history if not provided
                db_history = self.conversation_repo.get_content_messages(
                    content_item_id, history_limit
                )
                history_dicts = [
                    {"sender": msg.sender, "message": msg.message} for msg in db_history
                ]

            # Use LLM client for chat
            response = self.llm_client.generate_chat_response(
                article_title=title,
                article_summary=summary,
                conversation_history=history_dicts,
                user_message=user_message,
            )

            return {"response": response.content, "tokens_used": response.tokens_used}

        except Exception as e:
            logger.error(f"Error generating AI response: {str(e)}")
            raise ChatGenerationError("Failed to generate AI response", details=str(e)) from e

    def save_conversation(
        self, content_item_id: int, user_message: str, ai_response: str
    ) -> Dict[str, Any]:
        """
        Save user message and AI response to conversation history.

        Args:
            content_item_id: ID of the content item being discussed
            user_message: The user's message
            ai_response: The AI's response

        Returns:
            Dict with 'user_id' and 'ai_id' conversation record IDs
        """
        user_conv = self.conversation_repo.add_user_message(content_item_id, user_message)
        ai_conv = self.conversation_repo.add_ai_message(content_item_id, ai_response)

        return {"user_id": user_conv.id, "ai_id": ai_conv.id}
