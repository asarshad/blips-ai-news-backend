"""
LLM provider abstraction layer.

Supports multiple LLM providers (OpenAI, Mistral) with a unified interface.
Switch providers via LLM_PROVIDER environment variable.
"""

import os
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


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
        self.openai = openai
        
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model
        
        if not self._is_valid_key(self.api_key):
            logger.warning("OpenAI API key is missing or invalid. AI features will be unavailable.")
            self.api_key = None
        else:
            self.openai.api_key = self.api_key
    
    def _is_valid_key(self, key: Optional[str]) -> bool:
        if not key or key.strip() == "":
            return False
        if key in ("your-openai-api-key-here", "sk-xxx"):
            return False
        return True
    
    def is_configured(self) -> bool:
        return self.api_key is not None
    
    def get_provider_name(self) -> str:
        return "openai"
    
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise Exception("OpenAI API key is not configured. Set OPENAI_API_KEY environment variable.")
        
        try:
            api_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
            
            response = self.openai.chat.completions.create(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=self.model,
                provider="openai"
            )
        except Exception as e:
            logger.error(f"OpenAI chat error: {str(e)}")
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
    
    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise Exception("Mistral API key is not configured. Set MISTRAL_API_KEY environment variable.")
        
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
        except Exception as e:
            logger.error(f"Mistral chat error: {str(e)}")
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
        
        Args:
            messages: List of ChatMessage objects
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0-2)
            
        Returns:
            ChatResponse with content and usage info
        """
        return self._client.chat(messages, max_tokens, temperature)
    
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
            raise Exception(f"{self.get_provider()} API key is not configured")
        
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
            raise Exception(f"{self.get_provider()} API key is not configured")
        
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
                raise Exception("Empty summary returned from LLM")
            
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
