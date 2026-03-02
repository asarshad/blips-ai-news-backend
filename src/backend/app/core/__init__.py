"""
Core module - configuration, exceptions, and logging.

Note: Dependencies (get_db, get_redis) are not exported here to avoid
circular imports. Import them directly from app.core.dependencies.
"""

from app.core.config import settings
from app.core.exceptions import (
    AppException,
    ArticleNotFoundError,
    ChatGenerationError,
    ExternalServiceError,
    FeedFetchError,
    NotFoundError,
    QuotaExceededError,
    SummarizationError,
    ValidationError,
    VideoNotFoundError,
)
from app.core.logging import get_logger, setup_logging

__all__ = [
    "settings",
    "get_logger",
    "setup_logging",
    "AppException",
    "NotFoundError",
    "QuotaExceededError",
    "ExternalServiceError",
    "ValidationError",
    "ArticleNotFoundError",
    "VideoNotFoundError",
    "SummarizationError",
    "ChatGenerationError",
    "FeedFetchError",
]

