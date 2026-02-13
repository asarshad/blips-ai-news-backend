"""
LLM provider abstraction layer.

Supports multiple LLM providers (OpenAI, Mistral) with a unified interface.
Switch providers via LLM_PROVIDER environment variable.
Includes retry logic, request timeouts, and daily cost tracking.
"""

import time
from abc import ABC, abstractmethod
from datetime import date, timezone
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Timeout for individual LLM API calls (seconds)
LLM_REQUEST_TIMEOUT = int(settings.LLM_REQUEST_TIMEOUT) if hasattr(settings, "LLM_REQUEST_TIMEOUT") else 30

# Daily cost ceiling (USD) — tracked in Redis
LLM_DAILY_COST_CEILING = float(
    getattr(settings, "LLM_DAILY_COST_CEILING", 5.0)
)

# Approximate cost per 1K tokens (input+output blended) for budgeting
_TOKEN_COST_PER_1K = {
    "openai": 0.00030,   # gpt-4o-mini blended
    "mistral": 0.00025,  # mistral-small blended
    "fake": 0.0,
}


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    MISTRAL = "mistral"
    FAKE = "fake"  # For testing - deterministic responses, no API calls


@dataclass
class ChatMessage:
    """Represents a message in a chat conversation."""
    role: str  # "system", "user", or "assistant"
    content: str


@dataclass
class ChatResponse:
    """Response from LLM chat completion."""
    content: str
    tokens_used: int
    model: str
    provider: str


@dataclass
class SummaryResult:
    """Result from article summarization."""
    summary: str
    tags: List[str]


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        pass
    
    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        """Send a chat completion request."""
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name."""
        pass


class OpenAILLMClient(BaseLLMClient):
    """OpenAI implementation of LLM client."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        import openai
        
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model
        self._client = None
        
        if not self._is_valid_key(self.api_key):
            logger.warning("OpenAI API key is missing or invalid. AI features will be unavailable.")
            self.api_key = None
        else:
            self._client = openai.OpenAI(
                api_key=self.api_key,
                timeout=LLM_REQUEST_TIMEOUT,
            )
    
    def _is_valid_key(self, key: Optional[str]) -> bool:
        if not key or key.strip() == "":
            return False
        if key in ("your-openai-api-key-here", "sk-xxx"):
            return False
        return True
    
    def is_configured(self) -> bool:
        return self._client is not None
    
    def get_provider_name(self) -> str:
        return "openai"
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        reraise=True,
    )
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise RuntimeError("OpenAI API key is not configured. Set OPENAI_API_KEY environment variable.")
        
        try:
            api_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
            
            response = self._client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            
            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=self.model,
                provider="openai"
            )
        except (ConnectionError, TimeoutError):
            raise  # let tenacity retry
        except Exception as e:
            logger.error(f"OpenAI chat error: {type(e).__name__}: {e}")
            raise


class MistralLLMClient(BaseLLMClient):
    """Mistral AI implementation of LLM client."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "mistral-small-latest"):
        self.api_key = api_key or settings.MISTRAL_API_KEY
        self.model = model
        self.client = None
        
        if not self._is_valid_key(self.api_key):
            logger.warning("Mistral API key is missing or invalid. AI features will be unavailable.")
            self.api_key = None
        else:
            try:
                from mistralai import Mistral
                self.client = Mistral(api_key=self.api_key)
            except ImportError:
                logger.error("mistralai package not installed. Run: pip install mistralai")
                self.api_key = None
    
    def _is_valid_key(self, key: Optional[str]) -> bool:
        if not key or key.strip() == "":
            return False
        if key in ("your-mistral-api-key-here",):
            return False
        return True
    
    def is_configured(self) -> bool:
        return self.api_key is not None and self.client is not None
    
    def get_provider_name(self) -> str:
        return "mistral"
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        reraise=True,
    )
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise RuntimeError("Mistral API key is not configured. Set MISTRAL_API_KEY environment variable.")
        
        try:
            api_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
            
            response = self.client.chat.complete(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=self.model,
                provider="mistral"
            )
        except (ConnectionError, TimeoutError):
            raise  # let tenacity retry
        except Exception as e:
            logger.error(f"Mistral chat error: {type(e).__name__}: {e}")
            raise


class LLMClient:
    """
    Unified LLM client that delegates to the configured provider.
    
    Usage:
        client = LLMClient()  # Uses LLM_PROVIDER env var
        client = LLMClient(provider="mistral")  # Force specific provider
        
        response = client.chat([ChatMessage(role="user", content="Hello")])
    """
    
    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialize LLM client with specified or configured provider.
        
        Args:
            provider: LLM provider ("openai", "mistral", or "fake"). Defaults to LLM_PROVIDER env var.
            api_key: API key for the provider. Defaults to provider-specific env var.
            model: Model to use. Defaults to provider-specific default.
        """
        provider_name = provider or settings.LLM_PROVIDER
        
        if provider_name == LLMProvider.FAKE or provider_name == "fake":
            from app.integrations.fake_llm import FakeLLMClient
            self._client = FakeLLMClient()
        elif provider_name == LLMProvider.MISTRAL:
            default_model = model or settings.MISTRAL_MODEL
            self._client = MistralLLMClient(api_key=api_key, model=default_model)
        else:
            # Default to OpenAI
            default_model = model or settings.OPENAI_MODEL
            self._client = OpenAILLMClient(api_key=api_key, model=default_model)
        
        logger.info(f"LLM client initialized with provider: {self._client.get_provider_name()}")
    
    def is_configured(self) -> bool:
        """Check if the underlying client is properly configured."""
        return self._client.is_configured()
    
    def get_provider(self) -> str:
        """Get the current provider name."""
        return self._client.get_provider_name()
    
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        """
        Send a chat completion request to the configured provider.
        
        Enforces daily cost ceiling via Redis counter. Raises RuntimeError
        if the ceiling has been reached.
        
        Args:
            messages: List of ChatMessage objects
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0-2)
            
        Returns:
            ChatResponse with content and usage info
        """
        # --- Daily cost ceiling check ---
        self._enforce_cost_ceiling()

        response = self._client.chat(messages, max_tokens, temperature)

        # --- Track token spend ---
        self._record_tokens(response.tokens_used, response.provider)

        return response

    # ------------------------------------------------------------------
    # Cost tracking helpers
    # ------------------------------------------------------------------

    def _cost_redis_key(self) -> str:
        """Redis key for today's token counter."""
        today = date.today().isoformat()
        return f"llm:tokens:{today}"

    def _enforce_cost_ceiling(self) -> None:
        """Raise RuntimeError if daily estimated spend exceeds ceiling."""
        if LLM_DAILY_COST_CEILING <= 0:
            return  # disabled
        try:
            from app.core.dependencies import get_redis
            r = get_redis()
            raw = r.get(self._cost_redis_key())
            if raw is not None:
                total_tokens = int(raw)
                provider = self._client.get_provider_name()
                cost_per_1k = _TOKEN_COST_PER_1K.get(provider, 0.0003)
                estimated_cost = (total_tokens / 1000.0) * cost_per_1k
                if estimated_cost >= LLM_DAILY_COST_CEILING:
                    logger.warning(
                        f"LLM daily cost ceiling reached: ${estimated_cost:.4f} >= ${LLM_DAILY_COST_CEILING}"
                    )
                    raise RuntimeError(
                        f"LLM daily cost ceiling of ${LLM_DAILY_COST_CEILING} reached"
                    )
        except RuntimeError:
            raise
        except Exception as e:
            # If Redis is down, allow the request rather than blocking AI entirely
            logger.warning(f"Cost ceiling check failed (allowing request): {e}")

    def _record_tokens(self, tokens: int, provider: str) -> None:
        """Increment today's token counter in Redis."""
        if tokens <= 0:
            return
        try:
            from app.core.dependencies import get_redis
            r = get_redis()
            key = self._cost_redis_key()
            r.incrby(key, tokens)
            r.expire(key, 90_000)  # 25 hours — auto-expire stale counters
        except Exception as e:
            logger.warning(f"Token tracking failed (non-fatal): {e}")
    
    def summarize_article(
        self,
        title: str,
        content: str,
        max_content_length: int = 4000
    ) -> SummaryResult:
        """
        Generate a summary and tags for an article.
        
        Args:
            title: Article title
            content: Article content
            max_content_length: Max chars of content to send
            
        Returns:
            SummaryResult with summary and tags
        """
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")
        
        truncated_content = content[:max_content_length]
        
        prompt = f"""
Article Title: {title}

Article Content: {truncated_content}

Task 1: Write a concise summary of this tech article in exactly 85-90 words. Keep it informative and engaging.

Task 2: Generate 3-5 relevant tags for this article. Tags should be lowercase, single words or hyphenated phrases.

Format your response exactly like this:
SUMMARY: [your summary here]
TAGS: tag1, tag2, tag3
"""
        
        try:
            response = self.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                max_tokens=300,
                temperature=0.5
            )
            
            # Parse response
            text = response.content
            summary = ""
            tags = []
            
            if "SUMMARY:" in text:
                parts = text.split("TAGS:")
                summary = parts[0].replace("SUMMARY:", "").strip()
                if len(parts) > 1:
                    tags = [t.strip().lower() for t in parts[1].split(",")]
            else:
                summary = text.strip()
            
            return SummaryResult(summary=summary, tags=tags)
            
        except Exception as e:
            logger.error(f"Article summarization error: {str(e)}")
            raise
    
    def summarize_video(
        self,
        title: str,
        description: str,
        max_length: int = 4000
    ) -> str:
        """
        Generate a summary for a video based on its description.
        
        Args:
            title: Video title
            description: Video description
            max_length: Max chars of description to send
            
        Returns:
            Summary string
        """
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")
        
        truncated_desc = description[:max_length]
        
        prompt = f"""
Video Title: {title}

Video Description: {truncated_desc}

Task: Write a concise summary of this video in exactly 85-90 words based on the description. 
Focus on the main topic and key points. Remove any channel promotion, "link in bio", or "subscribe" text.

Format your response as just the summary text.
"""
        
        try:
            response = self.chat(
                messages=[
                    ChatMessage(role="system", content="You are a tech journalist assistant that creates concise, informative summaries of tech videos."),
                    ChatMessage(role="user", content=prompt)
                ],
                max_tokens=200,
                temperature=0.5
            )
            
            summary = response.content.strip()
            if not summary:
                raise ValueError("Empty summary returned from LLM")
            
            return summary
            
        except Exception as e:
            logger.error(f"Video summarization error: {str(e)}")
            raise
    
    def generate_chat_response(
        self,
        article_title: str,
        article_summary: str,
        conversation_history: List[Dict[str, str]],
        user_message: str
    ) -> ChatResponse:
        """
        Generate an AI response for article chat.
        
        Args:
            article_title: Title of the article being discussed
            article_summary: Summary of the article
            conversation_history: Previous messages in the conversation
            user_message: The user's current message
            
        Returns:
            ChatResponse with AI's response
        """
        system_prompt = f"""You are an AI assistant that discusses tech news articles with users.
You are knowledgeable, helpful, and focused on the article topic.

Article Title: {article_title}
Article Summary: {article_summary}

Keep responses concise (max 3 paragraphs) and directly relevant to the article.
If asked about topics unrelated to the article, politely redirect to the article topic.
"""
        
        messages = [ChatMessage(role="system", content=system_prompt)]
        
        # Add conversation history
        for msg in conversation_history:
            role = "user" if msg.get("sender") == "user" else "assistant"
            messages.append(ChatMessage(role=role, content=msg.get("message", "")))
        
        # Add current user message
        messages.append(ChatMessage(role="user", content=user_message))
        
        return self.chat(messages, max_tokens=300, temperature=0.7)


# Convenience function for backward compatibility
def get_llm_client(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None
) -> LLMClient:
    """
    Get an LLM client instance.
    
    This is the recommended way to get an LLM client throughout the application.
    """
    return LLMClient(provider=provider, api_key=api_key, model=model)
