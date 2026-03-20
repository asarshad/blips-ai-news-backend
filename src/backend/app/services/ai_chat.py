from typing import Any, Dict, List, Optional

from app.core.exceptions import ArticleNotFoundError, ChatGenerationError
from app.core.logging import get_logger
from app.integrations.llm_client import LLMClient
from app.repositories.content_repo import ContentItemRepository

logger = get_logger(__name__)


class AiChatService:
    """Service for AI-powered chat about content items."""

    def __init__(
        self,
        content_repo: ContentItemRepository,
        llm_client: Optional[LLMClient] = None,
    ):
        self.content_repo = content_repo
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
            if history is not None:
                history_dicts = history
            else:
                # Chats are device-local only; never hydrate shared server-side
                # history by content item.
                history_dicts = []

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
