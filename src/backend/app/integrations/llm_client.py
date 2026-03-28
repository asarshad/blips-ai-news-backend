"""
LLM provider abstraction layer.

Supports multiple LLM providers (OpenAI, Mistral) with a unified interface.
Switch providers via LLM_PROVIDER environment variable.
Includes retry logic, request timeouts, and daily cost tracking.
"""
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from redis.exceptions import RedisError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import PINNED_OPENAI_MODEL, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Timeout for individual LLM API calls (seconds)
LLM_REQUEST_TIMEOUT = (
    int(settings.LLM_REQUEST_TIMEOUT) if hasattr(settings, "LLM_REQUEST_TIMEOUT") else 30
)

# Daily cost ceiling (USD) — tracked in Redis
LLM_DAILY_COST_CEILING = float(getattr(settings, "LLM_DAILY_COST_CEILING", 5.0))

# Approximate cost per 1K tokens (input+output blended) for budgeting
_TOKEN_COST_PER_1K = {
    "openai": 0.00020,  # gpt-5-nano approximate blended rate
    "mistral": 0.00025,  # mistral-small blended
    "fake": 0.0,
}

_IMAGE_URL_RESPONSE_RE = re.compile(
    r"IMAGE_URL:\s*(?P<value>\S+)",
    re.IGNORECASE,
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_VIDEO_TECH_RELEVANCE_VALUES = {"none", "incidental", "meaningful", "primary"}


def normalize_video_summary_output(summary_text: Optional[str]) -> Optional[str]:
    """Normalize generated video summaries while enforcing configured bounds."""
    cleaned = " ".join((summary_text or "").split()).strip()
    if not cleaned:
        return None

    max_words = max(1, int(settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS))
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned

    return " ".join(words[:max_words]).rstrip(" ,;:-")


def is_video_summary_acceptable(summary_text: Optional[str]) -> bool:
    """Return True when a generated video summary meets the configured floor."""
    normalized = normalize_video_summary_output(summary_text)
    if not normalized:
        return False

    min_words = max(1, int(settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS))
    return len(normalized.split()) >= min_words


def normalize_video_tech_relevance(value: Optional[str]) -> Optional[str]:
    """Normalize classifier labels so the rest of the pipeline can compare reliably."""
    normalized = str(value or "").strip().lower()
    if normalized in _VIDEO_TECH_RELEVANCE_VALUES:
        return normalized
    return None


def normalize_video_classifier_confidence(value: object) -> Optional[float]:
    """Clamp classifier confidence into [0, 1]."""
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(normalized, 0.0), 1.0)


def normalize_video_classifier_reason(value: Optional[str]) -> Optional[str]:
    """Store compact, user-readable classifier reasons."""
    cleaned = " ".join((value or "").split()).strip()
    if not cleaned:
        return None
    return cleaned[:255]


def _strip_json_fence(payload: str) -> str:
    return _JSON_FENCE_RE.sub("", payload.strip())


def _load_mistral_client_class():
    """Resolve the Mistral SDK client across supported package layouts."""
    try:
        import mistralai
    except ImportError:
        raise

    client_cls = getattr(mistralai, "Mistral", None)
    if client_cls is not None:
        return client_cls

    try:
        from mistralai.client import Mistral as client_cls
    except ImportError as exc:
        raise ImportError("mistralai package installed, but Mistral client is unavailable") from exc

    return client_cls


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
    """Result from article/video summarization."""

    summary: str
    tags: List[str]
    conversation_starters: Optional[Dict[str, List[str]]] = None
    tech_relevance: Optional[str] = None
    tech_relevance_confidence: Optional[float] = None
    tech_relevance_reason: Optional[str] = None
    is_mixed_roundup: Optional[bool] = None


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        pass

    @abstractmethod
    def chat(
        self, messages: List[ChatMessage], max_tokens: int = 300, temperature: float = 0.7
    ) -> ChatResponse:
        """Send a chat completion request."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name."""
        pass


class OpenAILLMClient(BaseLLMClient):
    """OpenAI implementation of LLM client."""

    # Transient exception types that should trigger a retry.
    _RETRYABLE: tuple = ()  # populated in __init__ after import
    _REASONING_EFFORT = "minimal"

    def __init__(self, api_key: Optional[str] = None, model: str = PINNED_OPENAI_MODEL):
        import openai

        # Build the retryable tuple once at init time.
        self._RETRYABLE = (
            ConnectionError,
            TimeoutError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )

        self.api_key = api_key or settings.OPENAI_API_KEY
        requested_model = model or settings.OPENAI_MODEL
        self.model = PINNED_OPENAI_MODEL
        self._client = None

        if requested_model != self.model:
            logger.warning(
                "Ignoring OpenAI model override '%s'; using pinned model '%s'",
                requested_model,
                self.model,
            )

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
        self, messages: List[ChatMessage], max_tokens: int = 300, temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise RuntimeError(
                "OpenAI API key is not configured. Set OPENAI_API_KEY environment variable."
            )

        try:
            instructions = "\n\n".join(
                msg.content for msg in messages if msg.role == "system"
            ).strip()
            input_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
                if msg.role != "system"
            ]

            request_kwargs = {
                "model": self.model,
                "input": input_messages,
                "max_output_tokens": max_tokens,
                "reasoning": {"effort": self._REASONING_EFFORT},
                "store": False,
            }
            if instructions:
                request_kwargs["instructions"] = instructions
            if temperature != 0.7:
                logger.debug(
                    "Ignoring OpenAI temperature override for pinned GPT-5 model '%s'",
                    self.model,
                )

            response = self._client.responses.create(**request_kwargs)
            content = (response.output_text or "").strip()
            if not content:
                raise ValueError("Empty text output returned from OpenAI Responses API")

            return ChatResponse(
                content=content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=getattr(response, "model", self.model),
                provider="openai",
            )
        except tuple(self._RETRYABLE):
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
            logger.warning(
                "Mistral API key is missing or invalid. AI features will be unavailable."
            )
            self.api_key = None
        else:
            try:
                mistral_client_cls = _load_mistral_client_class()

                self.client = mistral_client_cls(
                    api_key=self.api_key,
                    timeout_ms=LLM_REQUEST_TIMEOUT * 1000,
                )
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
        self, messages: List[ChatMessage], max_tokens: int = 300, temperature: float = 0.7
    ) -> ChatResponse:
        if not self.is_configured():
            raise RuntimeError(
                "Mistral API key is not configured. Set MISTRAL_API_KEY environment variable."
            )

        try:
            api_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

            response = self.client.chat.complete(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=self.model,
                provider="mistral",
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
        model: Optional[str] = None,
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
        self, messages: List[ChatMessage], max_tokens: int = 300, temperature: float = 0.7
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
        except RedisError as e:
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
        except RedisError as e:
            logger.warning(f"Token tracking failed (non-fatal): {e}")

    def summarize_article(
        self, title: str, content: str, max_content_length: int = 4000
    ) -> SummaryResult:
        """
        Generate a summary, tags, and conversation starters for an article.

        All three outputs come from a single LLM call to avoid extra latency
        and ensure starters are always present when a summary is present.

        Args:
            title: Article title
            content: Article content
            max_content_length: Max chars of content to send

        Returns:
            SummaryResult with summary, tags, and conversation_starters
        """
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")

        truncated_content = content[:max_content_length]

        prompt = f"""
Article Title: {title}

Article Content: {truncated_content}

Perform ALL three tasks below in a single response.

Task 1: Write a concise summary of this tech article in between {settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS} and {settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words. Keep it informative and engaging. If the first draft would be shorter, add concrete factual detail from the article until it reaches at least {settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS} words. Never exceed {settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words.

Task 2: Generate 3-5 relevant tags for this article. Tags should be lowercase, single words or hyphenated phrases.

Task 3: Generate exactly 3 conversation-starter questions about this specific article. Each question must:
- Reference the specific topic or title
- Be at most 120 characters
- Encourage deeper discussion

Format your response exactly like this:
SUMMARY: [your summary here]
TAGS: tag1, tag2, tag3
STARTERS: question1 | question2 | question3
"""

        try:
            response = self.chat(
                messages=[ChatMessage(role="user", content=prompt)], max_tokens=450, temperature=0.5
            )

            # Parse response
            text = response.content
            summary = ""
            tags = []
            starters = None

            if "SUMMARY:" in text:
                # Split into sections
                parts = text.split("TAGS:")
                summary = parts[0].replace("SUMMARY:", "").strip()
                if len(parts) > 1:
                    tag_section = parts[1]
                    # Separate TAGS from STARTERS
                    if "STARTERS:" in tag_section:
                        tag_part, starter_part = tag_section.split("STARTERS:", 1)
                        tags = [t.strip().lower() for t in tag_part.split(",") if t.strip()]
                        raw_starters = [s.strip() for s in starter_part.split("|") if s.strip()]
                        # Truncate each starter to 120 chars and take up to 5
                        raw_starters = [
                            s[:117] + "..." if len(s) > 120 else s for s in raw_starters
                        ]
                        if raw_starters:
                            starters = {
                                "starters": raw_starters[:5],
                                "fallback": [
                                    "What are the main points of this?",
                                    "Can you summarize this for me?",
                                    "What should I know about this topic?",
                                ],
                            }
                    else:
                        tags = [t.strip().lower() for t in tag_section.split(",") if t.strip()]
            else:
                summary = text.strip()

            return SummaryResult(summary=summary, tags=tags, conversation_starters=starters)

        except (RuntimeError, ValueError) as e:
            logger.error(f"Article summarization error: {str(e)}")
            raise

    def summarize_video(
        self, title: str, description: str, max_length: int = 4000
    ) -> SummaryResult:
        """
        Generate a summary and conversation starters for a video.

        Both come from a single LLM call. Returns a SummaryResult so the
        caller gets starters inline, matching the article path.

        Args:
            title: Video title
            description: Video description
            max_length: Max chars of description to send

        Returns:
            SummaryResult with summary and conversation_starters
        """
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")

        truncated_desc = description[:max_length]

        prompt = f"""
Video Title: {title}

Video Description: {truncated_desc}

Return one JSON object with these keys only:
- "tech_relevance": one of "none", "incidental", "meaningful", "primary"
- "confidence": float between 0 and 1
- "is_mixed_roundup": boolean
- "reason": short explanation (max 25 words)
- "summary": string or null
- "starters": array of exactly 3 strings when summary is present, otherwise []

Classification rules:
- "primary" means the video is mainly about technology, products, software, hardware, AI, developer tools, cloud, cybersecurity, or the tech industry.
- "meaningful" means technology is not the whole story, but it materially affects why the story matters.
- "incidental" means technology is mentioned, but it is a minor supporting detail.
- "none" means the video has no meaningful tech angle for a tech-news feed.
- Medical, politics, sports, celebrity, crime, war, or general-news stories are NOT tech videos just because they mention AI, an app, social media, or a cloud provider.
- Set "is_mixed_roundup" to true when the video bundles multiple unrelated general-news stories into one omnibus segment.

Summary rules:
- If tech_relevance is "none" OR is_mixed_roundup is true, set "summary" to null and "starters" to [].
- Otherwise write a concise summary between {settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS} and {settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words.
- If the first draft would be shorter, add concrete factual detail from the description until it reaches at least {settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS} words.
- Never exceed {settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words.
- Remove channel promotion, "link in bio", or "subscribe" text.

Starter rules:
- Each starter must reference the specific topic or title.
- Each starter must be at most 120 characters.
- Each starter should encourage deeper discussion.

Return JSON only. Do not wrap it in markdown.
"""

        try:
            response = self.chat(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You are a careful tech-news editor. "
                            "Classify how relevant a video is to a tech-news feed and summarize it only when appropriate."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                max_tokens=350,
                temperature=0.5,
            )

            text = response.content.strip()
            if not text:
                raise ValueError("Empty response returned from LLM")

            try:
                payload = json.loads(_strip_json_fence(text))
                if not isinstance(payload, dict):
                    raise ValueError("Video classifier returned a non-object payload")

                tech_relevance = normalize_video_tech_relevance(payload.get("tech_relevance"))
                confidence = normalize_video_classifier_confidence(payload.get("confidence"))
                is_mixed_roundup = payload.get("is_mixed_roundup")
                reason = normalize_video_classifier_reason(payload.get("reason"))

                raw_summary = payload.get("summary")
                normalized_summary = normalize_video_summary_output(raw_summary)

                raw_starters = payload.get("starters")
                starters = None
                if isinstance(raw_starters, list):
                    sanitized = []
                    for value in raw_starters:
                        cleaned = " ".join(str(value or "").split()).strip()
                        if not cleaned:
                            continue
                        sanitized.append(
                            cleaned[:117] + "..." if len(cleaned) > 120 else cleaned
                        )
                    if sanitized:
                        starters = {
                            "starters": sanitized[:3],
                            "fallback": [
                                "What are the main points of this?",
                                "Can you summarize this for me?",
                                "What should I know about this topic?",
                            ],
                        }

                should_skip_summary = bool(is_mixed_roundup) or tech_relevance == "none"
            except json.JSONDecodeError:
                tech_relevance = None
                confidence = None
                is_mixed_roundup = None
                reason = None
                normalized_summary = ""
                starters = None
                should_skip_summary = False

                if "SUMMARY:" in text:
                    if "STARTERS:" in text:
                        parts = text.split("STARTERS:", 1)
                        normalized_summary = normalize_video_summary_output(
                            parts[0].replace("SUMMARY:", "").strip()
                        ) or ""
                        raw_starters = [s.strip() for s in parts[1].split("|") if s.strip()]
                        raw_starters = [
                            s[:117] + "..." if len(s) > 120 else s for s in raw_starters
                        ]
                        if raw_starters:
                            starters = {
                                "starters": raw_starters[:3],
                                "fallback": [
                                    "What are the main points of this?",
                                    "Can you summarize this for me?",
                                    "What should I know about this topic?",
                                ],
                            }
                    else:
                        normalized_summary = normalize_video_summary_output(
                            text.replace("SUMMARY:", "").strip()
                        ) or ""
                else:
                    normalized_summary = normalize_video_summary_output(text) or ""

            if not should_skip_summary and not normalized_summary:
                raise ValueError("Empty summary returned from LLM for tech-relevant video")

            return SummaryResult(
                summary=normalized_summary or "",
                tags=[],
                conversation_starters=starters,
                tech_relevance=tech_relevance,
                tech_relevance_confidence=confidence,
                tech_relevance_reason=reason,
                is_mixed_roundup=bool(is_mixed_roundup) if is_mixed_roundup is not None else None,
            )

        except (RuntimeError, ValueError) as e:
            logger.error(f"Video summarization error: {str(e)}")
            raise

    def extract_article_image_url(
        self,
        *,
        article_url: str,
        title: str,
        document: str,
    ) -> Optional[str]:
        """Extract the article's own hero image URL from provided page content."""
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")

        prompt = f"""
Article URL: {article_url}
Article Title: {title}

You are extracting the primary editorial image URL from the article document below.

Rules:
- Return only an image URL that is explicitly present in the provided document.
- Do not invent, guess, search the web, or rewrite a URL.
- Prefer the article's hero, featured, or lead image.
- Reject logos, icons, avatars, author photos, trackers, placeholders, thumbnails, and generic social/share images.
- If no trustworthy editorial image URL is present, return NONE.

Format your response exactly like this:
IMAGE_URL: <absolute-or-relative-url-from-document-or-NONE>

Article Document:
{document}
"""

        try:
            response = self.chat(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You extract article hero image URLs from provided page content. "
                            "Return only URLs present in the supplied document."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                max_tokens=120,
                temperature=0.1,
            )
        except (RuntimeError, ValueError) as e:
            logger.error(f"Article image extraction error: {str(e)}")
            raise

        content = (response.content or "").strip()
        if not content:
            return None

        match = _IMAGE_URL_RESPONSE_RE.search(content)
        candidate = match.group("value").strip() if match else content.splitlines()[0].strip()
        if candidate.upper() == "NONE":
            return None

        return candidate

    def generate_chat_response(
        self,
        article_title: str,
        article_summary: str,
        conversation_history: List[Dict[str, str]],
        user_message: str,
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
    provider: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None
) -> LLMClient:
    """
    Get an LLM client instance.

    This is the recommended way to get an LLM client throughout the application.
    """
    return LLMClient(provider=provider, api_key=api_key, model=model)
