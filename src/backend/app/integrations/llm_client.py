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
from typing import Any, Dict, List, Optional

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

# Token prices are USD per 1M tokens. Keep this small table near the call
# sites so the Redis cost ceiling tracks actual model choice, not just volume.
_OPENAI_TOKEN_PRICES_PER_1M = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
}
_PROVIDER_FALLBACK_PRICES_PER_1M = {
    "mistral": (0.25, 0.25),
    "fake": (0.0, 0.0),
}

_IMAGE_URL_RESPONSE_RE = re.compile(
    r"IMAGE_URL:\s*(?P<value>\S+)",
    re.IGNORECASE,
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_VIDEO_TECH_RELEVANCE_VALUES = {"none", "incidental", "meaningful", "primary"}
_BLIPS_TECH_RELEVANCE_VALUES = {"yes", "no"}
_VIDEO_CLASSIFIER_PAYLOAD_MARKERS = (
    '"tech_relevance"',
    '"confidence"',
    '"is_mixed_roundup"',
    '"reason"',
    '"summary"',
    '"starters"',
)


def normalize_video_summary_output(summary_text: Optional[str]) -> Optional[str]:
    """Normalize generated video summaries while enforcing configured bounds."""
    if not isinstance(summary_text, str):
        return None

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


def normalize_blips_tech_relevance(value: Optional[str]) -> Optional[str]:
    """Normalize the binary Blips news relevance label."""
    normalized = str(value or "").strip().lower()
    if normalized in _BLIPS_TECH_RELEVANCE_VALUES:
        return normalized
    return None


def _openai_prices_for_model(model: Optional[str]) -> tuple[float, float]:
    """Return OpenAI input/output prices for a model or snapshot alias."""
    model_name = (model or "").strip()
    if model_name in _OPENAI_TOKEN_PRICES_PER_1M:
        return _OPENAI_TOKEN_PRICES_PER_1M[model_name]

    for prefix, prices in sorted(
        _OPENAI_TOKEN_PRICES_PER_1M.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if model_name.startswith(f"{prefix}-"):
            return prices

    return (0.30, 0.30)


def _summary_word_count(text: Optional[str]) -> int:
    return len(" ".join((text or "").split()).split())


def _is_article_summary_acceptable(summary_text: Optional[str]) -> bool:
    cleaned = " ".join((summary_text or "").split()).strip()
    if not cleaned:
        return False
    min_words = max(1, int(settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS))
    return _summary_word_count(cleaned) >= min_words


def normalize_blips_tech_reason(value: Optional[str]) -> str:
    """Store compact, user-readable Blips tech relevance reasons."""
    return " ".join((value or "").split()).strip()[:255]


def looks_like_video_classifier_payload(text: Optional[str]) -> bool:
    """Detect classifier JSON blobs so they never leak into user-facing summaries."""
    if not isinstance(text, str):
        return False

    cleaned = " ".join(text.split()).strip().lower()
    if not cleaned:
        return False

    marker_hits = sum(1 for marker in _VIDEO_CLASSIFIER_PAYLOAD_MARKERS if marker in cleaned)
    return marker_hits >= 3 or (cleaned.startswith("{") and marker_hits >= 2)


def _strip_json_fence(payload: str) -> str:
    return _JSON_FENCE_RE.sub("", payload.strip())


def _openai_error_code(error: Exception) -> Optional[str]:
    """Extract a stable OpenAI API error code when available."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict):
            code = nested.get("code")
            if isinstance(code, str) and code:
                return code

    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code

    return None


def _is_previous_response_not_found(error: Exception) -> bool:
    """Return True when OpenAI rejects a chained response id lookup."""
    return _openai_error_code(error) == "previous_response_not_found"


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
    response_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


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


@dataclass
class BlipsTechRelevanceResult:
    """Result from Blips tech-news relevance classification."""

    is_blips_tech_relevant: str
    confidence: float
    reason: str


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
        temperature: float = 0.7,
        previous_response_id: Optional[str] = None,
        store: bool = False,
        model: Optional[str] = None,
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
        self.model = model or settings.OPENAI_MODEL or PINNED_OPENAI_MODEL
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
        temperature: float = 0.7,
        previous_response_id: Optional[str] = None,
        store: bool = False,
        model: Optional[str] = None,
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

            request_model = model or self.model
            request_kwargs = {
                "model": request_model,
                "input": input_messages,
                "max_output_tokens": max_tokens,
                "reasoning": {"effort": self._REASONING_EFFORT},
                "store": store,
            }
            if instructions:
                request_kwargs["instructions"] = instructions
            if previous_response_id:
                request_kwargs["previous_response_id"] = previous_response_id
            if temperature != 0.7:
                logger.debug(
                    "Ignoring OpenAI temperature override for GPT-5 model '%s'",
                    request_model,
                )

            response = self._client.responses.create(**request_kwargs)
            content = (response.output_text or "").strip()
            if not content:
                raise ValueError("Empty text output returned from OpenAI Responses API")
            usage = getattr(response, "usage", None)

            return ChatResponse(
                content=content,
                tokens_used=getattr(usage, "total_tokens", 0) if usage else 0,
                model=getattr(response, "model", request_model),
                provider="openai",
                response_id=getattr(response, "id", None),
                input_tokens=getattr(usage, "input_tokens", None) if usage else None,
                output_tokens=getattr(usage, "output_tokens", None) if usage else None,
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
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7,
        previous_response_id: Optional[str] = None,
        store: bool = False,
        model: Optional[str] = None,
    ) -> ChatResponse:
        if not self.is_configured():
            raise RuntimeError(
                "Mistral API key is not configured. Set MISTRAL_API_KEY environment variable."
            )

        try:
            api_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

            response = self.client.chat.complete(
                model=model or self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            usage = getattr(response, "usage", None)

            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=getattr(usage, "total_tokens", 0) if usage else 0,
                model=model or self.model,
                provider="mistral",
                input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
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
        self,
        messages: List[ChatMessage],
        max_tokens: int = 300,
        temperature: float = 0.7,
        previous_response_id: Optional[str] = None,
        store: bool = False,
        model: Optional[str] = None,
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

        response = self._client.chat(
            messages,
            max_tokens,
            temperature,
            previous_response_id=previous_response_id,
            store=store,
            model=model,
        )

        # --- Track token spend and estimated dollar spend ---
        self._record_usage(response)

        return response

    # ------------------------------------------------------------------
    # Cost tracking helpers
    # ------------------------------------------------------------------

    def _cost_redis_key(self) -> str:
        """Redis key for today's estimated dollar counter."""
        today = date.today().isoformat()
        return f"llm:cost_usd:{today}"

    def _tokens_redis_key(self) -> str:
        """Redis key for today's raw token counter, retained for diagnostics."""
        today = date.today().isoformat()
        return f"llm:tokens:{today}"

    def _rescue_calls_redis_key(self) -> str:
        """Redis key for today's full-model summary rescue calls."""
        today = date.today().isoformat()
        return f"llm:summary_rescue_calls:{today}"

    def _enforce_cost_ceiling(self) -> None:
        """Raise RuntimeError if daily estimated spend exceeds ceiling."""
        if LLM_DAILY_COST_CEILING <= 0:
            return  # disabled
        try:
            from app.core.dependencies import get_redis

            r = get_redis()
            raw = r.get(self._cost_redis_key())
            if raw is not None:
                estimated_cost = float(raw)
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

    def _estimate_response_cost_usd(self, response: ChatResponse) -> float:
        """Estimate request cost from usage details and the response model."""
        if response.tokens_used <= 0:
            return 0.0

        input_tokens = response.input_tokens
        output_tokens = response.output_tokens
        if input_tokens is None or output_tokens is None:
            output_tokens = int(response.tokens_used * 0.20)
            input_tokens = max(0, response.tokens_used - output_tokens)

        if response.provider == "openai":
            input_price, output_price = _openai_prices_for_model(response.model)
        else:
            input_price, output_price = _PROVIDER_FALLBACK_PRICES_PER_1M.get(
                response.provider,
                (0.30, 0.30),
            )

        return (input_tokens / 1_000_000.0) * input_price + (
            output_tokens / 1_000_000.0
        ) * output_price

    def _record_usage(self, response: ChatResponse) -> None:
        """Increment today's raw-token and estimated-dollar counters."""
        if response.tokens_used <= 0:
            return
        try:
            from app.core.dependencies import get_redis

            r = get_redis()
            token_key = self._tokens_redis_key()
            cost_key = self._cost_redis_key()
            r.incrby(token_key, response.tokens_used)
            r.expire(token_key, 90_000)  # 25 hours — auto-expire stale counters
            r.incrbyfloat(cost_key, self._estimate_response_cost_usd(response))
            r.expire(cost_key, 90_000)
        except RedisError as e:
            logger.warning(f"Token tracking failed (non-fatal): {e}")

    def _summary_models(
        self,
        *,
        surface: str,
        allow_rescue: bool,
    ) -> list[tuple[Optional[str], str]]:
        """Return the ordered summary model ladder for this provider."""
        if self.get_provider() != "openai":
            return [(None, "primary")]

        if surface == "article":
            models = [
                (settings.ARTICLE_SUMMARY_PRIMARY_MODEL, "primary"),
                (settings.ARTICLE_SUMMARY_FALLBACK_MODEL, "fallback"),
            ]
            rescue_model = settings.ARTICLE_SUMMARY_RESCUE_MODEL
        else:
            models = [
                (settings.VIDEO_SUMMARY_PRIMARY_MODEL, "primary"),
                (settings.VIDEO_SUMMARY_FALLBACK_MODEL, "fallback"),
            ]
            rescue_model = settings.VIDEO_SUMMARY_RESCUE_MODEL

        if allow_rescue and rescue_model:
            models.append((rescue_model, "rescue"))

        deduped: list[tuple[Optional[str], str]] = []
        seen = set()
        for model, tier in models:
            normalized = (model or "").strip() or None
            key = normalized or "__default__"
            if key in seen:
                continue
            seen.add(key)
            deduped.append((normalized, tier))
        return deduped

    def _try_consume_summary_rescue_budget(self) -> bool:
        """Consume one full-model rescue call if today's budget allows it."""
        limit = int(getattr(settings, "SUMMARY_RESCUE_DAILY_CALL_LIMIT", 50))
        if limit <= 0:
            return False
        try:
            from app.core.dependencies import get_redis

            r = get_redis()
            key = self._rescue_calls_redis_key()
            current = int(r.incr(key))
            r.expire(key, 90_000)
            if current > limit:
                try:
                    r.decr(key)
                except RedisError:
                    pass
                logger.warning(
                    "Summary rescue daily call limit reached: %s >= %s",
                    current - 1,
                    limit,
                )
                return False
            return True
        except RedisError as exc:
            logger.warning("Summary rescue budget check failed; allowing rescue: %s", exc)
            return True

    def _summary_chat(
        self,
        *,
        surface: str,
        tier: str,
        model: Optional[str],
        messages: List[ChatMessage],
        max_tokens: int,
        temperature: float,
    ) -> ChatResponse:
        """Run one summary attempt and log the chosen tier/model."""
        if tier == "rescue" and not self._try_consume_summary_rescue_budget():
            raise RuntimeError("Summary rescue daily call limit reached")
        logger.info(
            "Running %s summary attempt tier=%s model=%s provider=%s",
            surface,
            tier,
            model or "default",
            self.get_provider(),
        )
        return self.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )

    def _parse_article_summary_response(self, text: str) -> SummaryResult:
        """Parse the article summary/tags/starters contract."""
        summary = ""
        tags = []
        starters = None

        if "SUMMARY:" in text:
            parts = text.split("TAGS:")
            summary = parts[0].replace("SUMMARY:", "").strip()
            if len(parts) > 1:
                tag_section = parts[1]
                if "STARTERS:" in tag_section:
                    tag_part, starter_part = tag_section.split("STARTERS:", 1)
                    tags = [t.strip().lower() for t in tag_part.split(",") if t.strip()]
                    raw_starters = [s.strip() for s in starter_part.split("|") if s.strip()]
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

    def summarize_article(
        self,
        title: str,
        content: str,
        max_content_length: int = 4000,
        *,
        allow_rescue: bool = False,
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

        last_error: Exception | None = None
        for model, tier in self._summary_models(surface="article", allow_rescue=allow_rescue):
            try:
                response = self._summary_chat(
                    surface="article",
                    tier=tier,
                    model=model,
                    messages=[ChatMessage(role="user", content=prompt)],
                    max_tokens=450,
                    temperature=0.5,
                )
                result = self._parse_article_summary_response(response.content)
                if not _is_article_summary_acceptable(result.summary):
                    raise ValueError(
                        f"Article summary from {tier} model is empty or below "
                        f"{settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS} words"
                    )
                return result
            except (RuntimeError, ValueError) as e:
                last_error = e
                logger.warning(
                    "Article summarization attempt failed tier=%s model=%s: %s",
                    tier,
                    model or "default",
                    e,
                )

        if last_error is not None:
            logger.error(f"Article summarization error: {str(last_error)}")
            raise last_error
        raise ValueError("No article summary models configured")

    def _parse_video_summary_response(self, text: str) -> SummaryResult:
        """Parse and validate the video JSON summary contract."""
        text = text.strip()
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
                    sanitized.append(cleaned[:117] + "..." if len(cleaned) > 120 else cleaned)
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
            if looks_like_video_classifier_payload(text):
                raise ValueError("Malformed JSON returned from LLM for video summary")

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

        if looks_like_video_classifier_payload(normalized_summary):
            raise ValueError("Structured classifier payload leaked into video summary")

        if not should_skip_summary and not normalized_summary:
            raise ValueError("Empty summary returned from LLM for tech-relevant video")

        if not should_skip_summary and not is_video_summary_acceptable(normalized_summary):
            raise ValueError(
                f"Video summary is below {settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS} words"
            )

        return SummaryResult(
            summary=normalized_summary or "",
            tags=[],
            conversation_starters=starters,
            tech_relevance=tech_relevance,
            tech_relevance_confidence=confidence,
            tech_relevance_reason=reason,
            is_mixed_roundup=bool(is_mixed_roundup) if is_mixed_roundup is not None else None,
        )

    def summarize_video(
        self,
        title: str,
        description: str,
        max_length: int = 4000,
        *,
        allow_rescue: bool = False,
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

Before sending the response, verify that it is valid JSON:
- Use double quotes for all keys and string values.
- Do not include trailing commas.
- Make sure braces and brackets are balanced.
- Do not add markdown fences, commentary, or any text before or after the JSON object.
- If the draft is not valid JSON, fix it before sending.

Return JSON only. Do not wrap it in markdown.
"""

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a careful tech-news editor. "
                    "Classify how relevant a video is to a tech-news feed and summarize it only when appropriate."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        last_error: Exception | None = None
        for model, tier in self._summary_models(surface="video", allow_rescue=allow_rescue):
            try:
                response = self._summary_chat(
                    surface="video",
                    tier=tier,
                    model=model,
                    messages=messages,
                    max_tokens=350,
                    temperature=0.5,
                )
                return self._parse_video_summary_response(response.content)
            except (RuntimeError, ValueError) as e:
                last_error = e
                logger.warning(
                    "Video summarization attempt failed tier=%s model=%s: %s",
                    tier,
                    model or "default",
                    e,
                )

        if last_error is not None:
            logger.error(f"Video summarization error: {str(last_error)}")
            raise last_error
        raise ValueError("No video summary models configured")

    def classify_blips_tech_relevance(
        self,
        *,
        title: str,
        summary: str,
        source: str,
        url: Optional[str] = None,
    ) -> BlipsTechRelevanceResult:
        """Classify whether a news item is relevant to a Blips tech-news audience."""
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")

        url_line = f"URL: {url}\n" if (url or "").strip() else ""
        prompt = f"""
You are classifying whether a news item is relevant for the audience of Blips, a tech-news product.

Your job is NOT to decide whether the story is "pure tech."
Your job is to decide whether the story is relevant to a tech-news audience.

Classification target:
- is_blips_tech_relevant: yes | no

Decision standard:
Be permissive, not strict.

Include stories that are directly about technology OR meaningfully relevant to a tech-news audience, including:
- major tech companies
- AI companies, models, chips, cloud, software, hardware
- social platforms, app stores, devices, internet infrastructure
- cybersecurity, privacy, developer ecosystems, open source
- regulation, litigation, antitrust, policy, bans, export controls, labor actions, or legislation affecting tech companies, platforms, AI, semiconductors, privacy, or internet services
- geopolitics or business news when it materially affects semiconductors, supply chains, cloud providers, platforms, telecom, or major tech firms
- earnings, M&A, leadership changes, strategy shifts, or market moves involving major technology companies

Exclude stories when the connection to tech is weak, incidental, or nonexistent, including:
- general world news with no meaningful tech angle
- politics, crime, war, protests, sports, entertainment, or celebrity news with no direct impact on tech companies, platforms, products, infrastructure, or regulation
- business news unrelated to technology or tech-adjacent industries

Important rule:
If a reasonable tech-news reader would likely care because the story affects tech companies, platforms, AI, chips, software, hardware, privacy, or internet regulation, classify it as yes.

Be especially careful not to reject:
- lawsuits against major tech companies
- antitrust or regulatory actions involving platforms, AI, chips, app stores, or privacy
- government policy affecting semiconductors, export controls, cloud, telecom, or social media
- geopolitics that materially affects the tech supply chain
- business or financial stories centered on major tech companies

Only classify as no when the tech relevance is clearly weak or absent.

Input:
Title: {title}
Summary: {summary}
Source: {source}
{url_line}
Output:
Return JSON only in this exact format:
{{
  "is_blips_tech_relevant": "yes" | "no",
  "confidence": 0.0-1.0,
  "reason": "short explanation"
}}

Reason requirements:
- Keep it brief and concrete
- Mention the tech angle if yes
- Mention why the tech connection is weak/absent if no

Return JSON only. Do not wrap it in markdown.
"""

        try:
            response = self.chat(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You classify whether a news item is relevant to a tech-news audience. "
                            "Be permissive and return JSON only."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                max_tokens=180,
                temperature=0.1,
            )

            text = response.content.strip()
            if not text:
                raise ValueError("Empty response returned from LLM")

            try:
                payload = json.loads(_strip_json_fence(text))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Malformed JSON returned from LLM for Blips tech relevance classification"
                ) from exc

            if not isinstance(payload, dict):
                raise ValueError("Blips tech relevance classifier returned a non-object payload")

            relevance = normalize_blips_tech_relevance(payload.get("is_blips_tech_relevant"))
            confidence = normalize_video_classifier_confidence(payload.get("confidence"))
            reason = normalize_blips_tech_reason(payload.get("reason"))

            if relevance is None or confidence is None:
                raise ValueError("Invalid Blips tech relevance classifier payload")

            return BlipsTechRelevanceResult(
                is_blips_tech_relevant=relevance,
                confidence=confidence,
                reason=reason,
            )
        except (RuntimeError, ValueError) as e:
            logger.error(f"Blips tech relevance classification error: {str(e)}")
            raise

    def extract_article_image_url(
        self,
        *,
        article_url: str,
        title: str,
        document: str,
        allow_logo_fallback: bool = False,
    ) -> Optional[str]:
        """Extract the article's own hero image URL from provided page content."""
        if not self.is_configured():
            raise RuntimeError(f"{self.get_provider()} API key is not configured")

        fallback_rules = ""
        if allow_logo_fallback:
            fallback_rules = """
- If no trustworthy editorial hero image is present, you may return a high-resolution company or product logo only as a last resort.
- Only use a logo when it appears to be the main visual for the article, not a tiny navigation/header/footer logo.
- Prefer logos that are large, centered, or otherwise clearly emphasized in the article content.
"""

        prompt = f"""
Article URL: {article_url}
Article Title: {title}

You are extracting the primary editorial image URL from the article document below.

Rules:
- Return only an image URL that is explicitly present in the provided document.
- Do not invent, guess, search the web, or rewrite a URL.
- Prefer the article's hero, featured, or lead image.
- Reject logos, icons, avatars, author photos, trackers, placeholders, thumbnails, and generic social/share images.
- Treat company or product logos as a last resort only when no better editorial image exists.
{fallback_rules}
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
        previous_response_id: Optional[str] = None,
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

        if previous_response_id and self.get_provider() == "openai":
            chained_messages = [
                *messages,
                ChatMessage(role="user", content=user_message),
            ]
            try:
                return self.chat(
                    chained_messages,
                    max_tokens=300,
                    temperature=0.7,
                    previous_response_id=previous_response_id,
                    store=True,
                )
            except Exception as error:
                if _is_previous_response_not_found(error):
                    logger.warning(
                        "OpenAI previous_response_id=%s was not found; falling back to history replay",
                        previous_response_id,
                    )
                else:
                    raise

        # Add conversation history
        for msg in conversation_history:
            role = "user" if msg.get("sender") == "user" else "assistant"
            messages.append(ChatMessage(role=role, content=msg.get("message", "")))

        # Add current user message
        messages.append(ChatMessage(role="user", content=user_message))

        return self.chat(
            messages,
            max_tokens=300,
            temperature=0.7,
            store=self.get_provider() == "openai",
        )


# Convenience function for backward compatibility
def get_llm_client(
    provider: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None
) -> LLMClient:
    """
    Get an LLM client instance.

    This is the recommended way to get an LLM client throughout the application.
    """
    return LLMClient(provider=provider, api_key=api_key, model=model)
