"""Integration layer for external services.

Exports are resolved lazily to avoid package-level import cycles between
integration clients and modules that share classifier helpers.
"""

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


def __getattr__(name):
    """Lazily resolve integration exports to avoid package cycles."""
    if name in {"ChatMessage", "ChatResponse", "LLMClient", "SummaryResult", "get_llm_client"}:
        from app.integrations.llm_client import (
            ChatMessage,
            ChatResponse,
            LLMClient,
            SummaryResult,
            get_llm_client,
        )

        return {
            "ChatMessage": ChatMessage,
            "ChatResponse": ChatResponse,
            "LLMClient": LLMClient,
            "SummaryResult": SummaryResult,
            "get_llm_client": get_llm_client,
        }[name]
    if name == "OpenAIClient":
        from app.integrations.openai_client import OpenAIClient

        return OpenAIClient
    if name == "RSSClient":
        from app.integrations.rss_client import RSSClient

        return RSSClient
    if name == "YouTubeClient":
        from app.integrations.youtube_client import YouTubeClient

        return YouTubeClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
