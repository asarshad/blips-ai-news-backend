"""
Structured error codes for API responses.

Each error code maps to a stable string identifier that mobile clients
can use to show contextual error messages. The backend's exception
handler attaches the code to every JSON error response.

The mobile app maps these codes -> user-facing messages, allowing the
backend to evolve error details without breaking the client UX.
"""

from enum import Enum


class ErrorCode(str, Enum):
    """Stable error codes included in every error response."""

    # 4xx – Client errors
    NOT_FOUND = "not_found"
    ARTICLE_NOT_FOUND = "article_not_found"
    VIDEO_NOT_FOUND = "video_not_found"
    CONTENT_NOT_FOUND = "content_not_found"
    VALIDATION_ERROR = "validation_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"

    # 5xx – Server errors
    INTERNAL_ERROR = "internal_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    LLM_QUOTA_EXCEEDED = "llm_quota_exceeded"
    LLM_CONFIGURATION_ERROR = "llm_configuration_error"
    FEED_FETCH_ERROR = "feed_fetch_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CIRCUIT_OPEN = "circuit_open"

    # Informational
    MAINTENANCE = "maintenance"


# Default user-facing messages (used by mobile as fallback)
ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NOT_FOUND: "The requested item was not found.",
    ErrorCode.ARTICLE_NOT_FOUND: "This article is no longer available.",
    ErrorCode.VIDEO_NOT_FOUND: "This video is no longer available.",
    ErrorCode.CONTENT_NOT_FOUND: "This content is no longer available.",
    ErrorCode.VALIDATION_ERROR: "Invalid request. Please check your input.",
    ErrorCode.QUOTA_EXCEEDED: "You've reached today's limit. Come back tomorrow!",
    ErrorCode.RATE_LIMITED: "Too many requests. Please slow down.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong. Please try again.",
    ErrorCode.EXTERNAL_SERVICE_ERROR: "An external service is temporarily down.",
    ErrorCode.LLM_QUOTA_EXCEEDED: "AI features are temporarily unavailable.",
    ErrorCode.LLM_CONFIGURATION_ERROR: "AI service is not configured.",
    ErrorCode.FEED_FETCH_ERROR: "Unable to fetch latest content.",
    ErrorCode.SERVICE_UNAVAILABLE: "Service is temporarily unavailable.",
    ErrorCode.CIRCUIT_OPEN: "This service is recovering. Please try again shortly.",
    ErrorCode.MAINTENANCE: "We're performing maintenance. Back soon!",
}
