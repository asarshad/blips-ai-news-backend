"""
Conversation Starters Service.

Generates AI-powered conversation starter questions for content items.
Starters are generated during ingestion or on-demand and persisted in DB.
"""

import json
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.integrations.llm_client import ChatMessage, LLMClient
from app.models.content import ContentItem, ContentType

logger = get_logger(__name__)


# Default fallback starters when AI generation fails
DEFAULT_FALLBACK_STARTERS = [
    "What are the main points of this?",
    "Can you summarize this for me?",
    "What should I know about this topic?",
]


class StarterGenerationError(Exception):
    """Raised when starter generation fails."""
    pass


def get_starter_prompt(title: str, summary: str, content_type: ContentType) -> str:
    """
    Build the prompt for generating conversation starters.
    
    Args:
        title: Content title
        summary: Content summary
        content_type: Type of content (ARTICLE, VIDEO, REEL)
        
    Returns:
        Formatted prompt string
    """
    content_type_label = content_type.value.lower()
    
    return f"""You are a helpful assistant that generates conversation starter questions for a tech news app.

Given the following {content_type_label}:
Title: {title}
Summary: {summary or "No summary available"}

Generate 3 conversation starter questions that:
1. Are specific to this content (reference the title/topic)
2. Encourage deeper discussion about the implications
3. Are conversational and engaging
4. Each question must be at most 120 characters long

Also provide 2-3 fallback questions that work for any content. Each fallback must also be at most 120 characters long.

Respond ONLY with valid JSON in this exact format:
{{
  "starters": [
    "Question about specific topic...",
    "Question about implications...",
    "Question about technical details..."
  ],
  "fallback": [
    "General question 1...",
    "General question 2..."
  ]
}}"""


def parse_starters_response(response_text: str) -> Dict[str, List[str]]:
    """
    Parse LLM response into structured starters.
    
    Args:
        response_text: Raw LLM response
        
    Returns:
        Dict with "starters" and "fallback" lists
        
    Raises:
        StarterGenerationError: If parsing fails
    """
    try:
        # Try to extract JSON from response
        # Handle case where LLM wraps in markdown code block
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        data = json.loads(text)
        
        starters = data.get("starters", [])
        fallback = data.get("fallback", DEFAULT_FALLBACK_STARTERS)
        
        # Validate
        if not isinstance(starters, list) or len(starters) == 0:
            raise StarterGenerationError("No starters in response")
        
        # Filter empty strings and truncate to 120 chars
        starters = [s.strip() for s in starters if isinstance(s, str) and s.strip()]
        starters = [s[:117] + "..." if len(s) > 120 else s for s in starters]
        
        fallback_list = fallback[:3] if fallback else DEFAULT_FALLBACK_STARTERS
        fallback_list = [s.strip() for s in fallback_list if isinstance(s, str) and s.strip()]
        fallback_list = [s[:117] + "..." if len(s) > 120 else s for s in fallback_list]
        
        return {
            "starters": starters[:5],  # Limit to 5
            "fallback": fallback_list if fallback_list else DEFAULT_FALLBACK_STARTERS,
        }
        
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse starters JSON: {e}")
        raise StarterGenerationError(f"Invalid JSON response: {e}") from e


class ConversationStartersService:
    """
    Service for generating and managing conversation starters.
    
    Usage:
        service = ConversationStartersService(llm_client)
        starters = service.generate_starters(content_item)
        
        # Or generate for a batch during ingestion
        service.generate_for_batch(content_items, db_session)
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Initialize the service.
        
        Args:
            llm_client: LLM client instance. If None, creates default.
        """
        self._llm_client = llm_client
    
    @property
    def llm_client(self) -> LLMClient:
        """Lazy-load LLM client."""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def generate_starters(
        self,
        content_item: ContentItem,
        force_regenerate: bool = False
    ) -> Dict[str, List[str]]:
        """
        Generate conversation starters for a content item.
        
        Args:
            content_item: The content item to generate starters for
            force_regenerate: If True, regenerate even if starters exist
            
        Returns:
            Dict with "starters" and "fallback" lists
        """
        # Return existing starters if available
        if content_item.conversation_starters and not force_regenerate:
            return content_item.conversation_starters
        
        # Check if LLM is configured
        if not self.llm_client.is_configured():
            logger.warning("LLM not configured, using default starters")
            return self._get_default_starters(content_item)
        
        try:
            prompt = get_starter_prompt(
                title=content_item.title,
                summary=content_item.summary or content_item.description or "",
                content_type=content_item.type
            )
            
            response = self.llm_client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                max_tokens=300,
                temperature=0.7
            )
            
            starters = parse_starters_response(response.content)
            logger.info(f"Generated starters for content_id={content_item.id}")
            return starters
            
        except Exception as e:
            logger.error(f"Failed to generate starters for content_id={content_item.id}: {e}")
            return self._get_default_starters(content_item)
    
    def _get_default_starters(self, content_item: ContentItem) -> Dict[str, List[str]]:
        """Generate default starters based on content metadata."""
        short_title = content_item.title[:40] + "..." if len(content_item.title) > 40 else content_item.title
        
        if content_item.type == ContentType.VIDEO:
            starters = [
                f"What are the key takeaways from '{short_title}'?",
                "Can you explain the main concepts?",
                "What practical applications does this have?",
            ]
        elif content_item.type == ContentType.REEL:
            starters = [
                f"What's the main point of '{short_title}'?",
                "Can you elaborate on this?",
            ]
        else:
            starters = [
                f"What are the implications of '{short_title}'?",
                "Can you break down the key points?",
                "How does this compare to similar developments?",
            ]
        
        return {
            "starters": starters,
            "fallback": DEFAULT_FALLBACK_STARTERS,
        }
    
    def generate_and_persist(
        self,
        content_item: ContentItem,
        force_regenerate: bool = False
    ) -> Dict[str, List[str]]:
        """
        Generate starters and update the content item in place.
        
        Note: Caller is responsible for committing the session.
        
        Args:
            content_item: Content item to update
            force_regenerate: If True, regenerate even if starters exist
            
        Returns:
            The generated starters
        """
        starters = self.generate_starters(content_item, force_regenerate)
        content_item.conversation_starters = starters
        return starters


# Singleton instance for convenience
_service_instance: Optional[ConversationStartersService] = None


def get_starters_service(llm_client: Optional[LLMClient] = None) -> ConversationStartersService:
    """Get or create the conversation starters service singleton."""
    global _service_instance
    if _service_instance is None or llm_client is not None:
        _service_instance = ConversationStartersService(llm_client)
    return _service_instance
