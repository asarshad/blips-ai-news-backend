"""
Fake LLM client for testing and local development.

This module provides a deterministic LLM implementation that:
- Returns predictable responses for testing
- Does not require API keys
- Supports all LLMClient operations

Enable via: LLM_PROVIDER=fake
"""

import json
import re
from typing import Dict, List

from app.core.logging import get_logger
from app.integrations.llm_client import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
)

logger = get_logger(__name__)


class FakeLLMClient(BaseLLMClient):
    """
    Fake LLM client that returns deterministic responses.

    Useful for:
    - Unit tests
    - Integration tests without external API calls
    - Local development without API keys
    - E2E testing with predictable outputs
    """

    def __init__(self):
        self._call_count = 0
        self._last_messages: List[ChatMessage] = []
        logger.info("FakeLLM initialized - deterministic responses enabled")

    def is_configured(self) -> bool:
        """FakeLLM is always configured."""
        return True

    def get_provider_name(self) -> str:
        return "fake"

    def reset(self) -> None:
        """Reset call counter (useful in tests)."""
        self._call_count = 0
        self._last_messages = []

    def get_call_count(self) -> int:
        """Get number of calls made (useful in tests)."""
        return self._call_count

    def get_last_messages(self) -> List[ChatMessage]:
        """Get last messages sent (useful in tests)."""
        return self._last_messages

    def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7,
        previous_response_id: str | None = None,
        store: bool = False,
        model: str | None = None,
    ) -> ChatResponse:
        """
        Return deterministic chat response based on message content.

        Detects request type from message content and returns appropriate response.
        """
        self._call_count += 1
        self._last_messages = messages

        # Get the last user message
        user_message = ""
        system_prompt = ""
        for msg in messages:
            if msg.role == "user":
                user_message = msg.content
            elif msg.role == "system":
                system_prompt = msg.content

        # Detect request type and return appropriate response
        response_content = self._generate_response(system_prompt, user_message)

        return ChatResponse(
            content=response_content,
            tokens_used=len(response_content.split()) * 2,  # Approximate
            model=model or "fake-model-v1",
            provider="fake",
        )

    def _generate_response(self, system_prompt: str, user_message: str) -> str:
        """Generate deterministic response based on detected request type."""

        combined = f"{system_prompt} {user_message}".lower()

        # Detect conversation starters request
        if "conversation starter" in combined or "suggested question" in combined:
            return self._generate_starters_response(user_message)

        if "starter prompts:" in combined and "return only valid json as an object" in combined:
            return self._generate_starter_answers_response(user_message)

        if "image_url:" in combined and "editorial image url" in combined:
            return self._generate_image_extraction_response(user_message)

        if '"is_blips_tech_relevant"' in combined:
            return self._generate_blips_tech_relevance_response(user_message)

        # Detect summarization request
        if "summary" in combined or "summarize" in combined:
            return self._generate_summary_response(user_message)

        # Detect chat/discussion request
        if "article title" in combined or "discusses tech news" in combined:
            return self._generate_chat_response(user_message)

        # Default response
        return "This is a deterministic response from FakeLLM for testing purposes."

    def _generate_starters_response(self, user_message: str) -> str:
        """Generate conversation starters JSON."""
        # Extract title if present in the message
        title_match = re.search(r'title[:\s]+["\']?([^"\']+)["\']?', user_message, re.IGNORECASE)
        title = title_match.group(1) if title_match else "this article"

        starters = {
            "starters": [
                f"What are the main implications of {title[:30]}...?",
                "Can you explain the key technical concepts mentioned?",
                "How does this compare to similar developments in the industry?",
            ],
            "fallback": [
                "What are the main points of this article?",
                "Can you summarize the key takeaways?",
                "What should I know about this topic?",
            ],
        }
        return json.dumps(starters)

    def _generate_summary_response(self, user_message: str) -> str:
        """Generate article/video summary (with starters when requested)."""
        if '"tech_relevance"' in user_message.lower() and '"is_mixed_roundup"' in user_message.lower():
            title_match = re.search(
                r"video title:\s*(.+?)(?:\n|$)",
                user_message,
                re.IGNORECASE,
            )
            title = (title_match.group(1).strip() if title_match else "").lower()
            if "roundup" in title or "arrest" in title:
                payload = {
                    "tech_relevance": "none",
                    "confidence": 0.96,
                    "is_mixed_roundup": "roundup" in title,
                    "reason": "General-news coverage without a meaningful tech angle.",
                    "summary": None,
                    "starters": [],
                }
            else:
                payload = {
                    "tech_relevance": "primary",
                    "confidence": 0.9,
                    "is_mixed_roundup": False,
                    "reason": "The video is mainly about technology developments.",
                    "summary": (
                        "This FakeLLM video summary explains a technology story with enough concrete "
                        "detail for tests, covering the main product update, the companies involved, "
                        "why developers or buyers should care, what tradeoffs remain, and how the "
                        "change could affect software teams, platform strategy, infrastructure costs, "
                        "security decisions, and future industry adoption."
                    ),
                    "starters": [
                        "What are the key innovations discussed?",
                        "How might this impact the industry?",
                        "What stands out most from this video?",
                    ],
                }
            return json.dumps(payload)

        # Check if it's requesting starters too (merged prompt)
        if "starters" in user_message.lower() and "tags" in user_message.lower():
            return """SUMMARY: This FakeLLM article summary gives tests a realistic technology-news paragraph with enough detail to satisfy the production word floor. It explains the main development, names the technical or business angle, describes why developers, companies, or users should care, and notes practical implications for adoption, competition, security, infrastructure, product strategy, and future market direction.
TAGS: technology, innovation, ai, software, testing
STARTERS: What are the key innovations discussed? | How might this impact the industry? | What do experts say about the long-term effects?"""

        if "starters" in user_message.lower():
            return """SUMMARY: This FakeLLM summary gives tests a realistic technology-news paragraph with enough detail to satisfy the configured word floor. It explains the main development, names the technical or business angle, describes why developers, companies, or users should care, and notes practical implications for adoption, competition, security, infrastructure, product strategy, and future market direction.
STARTERS: What are the key innovations discussed? | How might this impact the industry? | What do experts say about the long-term effects?"""

        if "tags" in user_message.lower():
            return """SUMMARY: This FakeLLM article summary gives tests a realistic technology-news paragraph with enough detail to satisfy the production word floor. It explains the main development, names the technical or business angle, describes why developers, companies, or users should care, and notes practical implications for adoption, competition, security, infrastructure, product strategy, and future market direction.
TAGS: technology, innovation, ai, software, testing"""

        return (
            "This FakeLLM summary gives tests a realistic technology-news paragraph with enough "
            "detail to satisfy the configured word floor. It explains the main development, names "
            "the technical or business angle, describes why developers, companies, or users should "
            "care, and notes practical implications for adoption, competition, security, "
            "infrastructure, product strategy, and future market direction."
        )

    def _generate_starter_answers_response(self, user_message: str) -> str:
        """Generate deterministic starter answers JSON."""
        prompts = []
        capture = False
        for line in user_message.splitlines():
            stripped = line.strip()
            if stripped.lower() == "starter prompts:":
                capture = True
                continue
            if not capture or not stripped.startswith("- "):
                continue
            prompt = stripped[2:].strip()
            if prompt:
                prompts.append(prompt)

        payload = {
            prompt: (
                f"Here is a concise answer to '{prompt}'. "
                "It focuses on the main implications of the story and the most important context for a tech-news reader."
            )
            for prompt in prompts
        }
        return json.dumps(payload)

    def _generate_blips_tech_relevance_response(self, user_message: str) -> str:
        """Generate deterministic Blips news relevance classification JSON."""
        def _extract(label: str) -> str:
            match = re.search(rf"^{label}:\s*(.+)$", user_message, re.IGNORECASE | re.MULTILINE)
            return match.group(1).strip() if match else ""

        combined = " ".join(
            [
                _extract("Title"),
                _extract("Summary"),
                _extract("Source"),
                _extract("URL"),
            ]
        ).lower()
        if any(
            keyword in combined
            for keyword in (
                "apple",
                "google",
                "microsoft",
                "meta",
                "amazon",
                "openai",
                "nvidia",
                "chip",
                "semiconductor",
                "ai ",
                "cloud",
                "app store",
                "antitrust",
                "privacy",
                "tiktok",
                "social media",
            )
        ):
            payload = {
                "is_blips_tech_relevant": "yes",
                "confidence": 0.92,
                "reason": "Directly affects a major tech company, platform, or core technology market.",
            }
        else:
            payload = {
                "is_blips_tech_relevant": "no",
                "confidence": 0.9,
                "reason": "General news item with no meaningful tech company, product, or policy angle.",
            }
        return json.dumps(payload)

    def _generate_chat_response(self, user_message: str) -> str:
        """Generate chat discussion response."""
        return "That's an interesting question about this article. Based on the content, here are some key points to consider:\n\n1. The main topic relates to current technology trends.\n2. There are potential implications for the broader industry.\n3. Experts have varying opinions on the long-term impact.\n\nWould you like me to elaborate on any of these points?"

    def _generate_image_extraction_response(self, user_message: str) -> str:
        """Return the first plausible image URL mentioned in the prompt."""
        match = re.search(
            r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^\s\"'<>]*)?",
            user_message,
            re.IGNORECASE,
        )
        if match:
            return f"IMAGE_URL: {match.group(0)}"
        return "IMAGE_URL: NONE"


def generate_starters_for_content(
    title: str, summary: str, content_type: str = "article"
) -> Dict[str, List[str]]:
    """
    Generate conversation starters for content.

    This is the deterministic version used by FakeLLM.
    Returns structured starters based on content metadata.

    Args:
        title: Content title
        summary: Content summary
        content_type: "article", "video", or "reel"

    Returns:
        Dict with "starters" and "fallback" lists
    """
    # Create title-specific starters
    short_title = title[:40] + "..." if len(title) > 40 else title

    if content_type == "video":
        starters = [
            f"What are the key takeaways from '{short_title}'?",
            "Can you explain the main concepts covered in this video?",
            "What practical applications does this topic have?",
        ]
    elif content_type == "reel":
        starters = [
            f"What's the main point of '{short_title}'?",
            "Can you elaborate on this topic?",
        ]
    else:  # article
        starters = [
            f"What are the implications of '{short_title}'?",
            "Can you break down the key points for me?",
            "How does this compare to similar recent developments?",
        ]

    fallback = [
        "What are the main points?",
        "Can you summarize this for me?",
        "What should I know about this?",
    ]

    return {"starters": starters, "fallback": fallback}
