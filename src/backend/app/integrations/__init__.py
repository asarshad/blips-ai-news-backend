"""
Integration layer for external services.

This module contains clients and wrappers for external APIs and services.
Each integration is isolated from business logic and can be mocked for testing.
"""

from app.integrations.openai_client import OpenAIClient
from app.integrations.llm_client import LLMClient, get_llm_client, ChatMessage, ChatResponse, SummaryResult
from app.integrations.rss_client import RSSClient
from app.integrations.youtube_client import YouTubeClient

__all__ = [
    "OpenAIClient",  # Deprecated: use LLMClient instead
    "LLMClient",
    "get_llm_client",
    "ChatMessage",
    "ChatResponse",
    "SummaryResult",
    "RSSClient",
    "YouTubeClient",
]
