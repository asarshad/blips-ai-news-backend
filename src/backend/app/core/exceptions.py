"""
Custom exception classes for the application.
These provide semantic error handling across the codebase.
"""

from typing import Optional, Union
from fastapi import HTTPException, status


class AppException(Exception):
    """Base exception for application errors."""
    
    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppException):
    """Raised when a requested resource is not found."""
    pass


class QuotaExceededError(AppException):
    """Raised when a user exceeds their usage quota."""
    pass


class ExternalServiceError(AppException):
    """Raised when an external service (OpenAI, RSS feed, etc.) fails."""
    pass


class ValidationError(AppException):
    """Raised when input validation fails."""
    pass


class ArticleNotFoundError(NotFoundError):
    """Raised when an article is not found."""
    
    def __init__(self, article_id: int):
        super().__init__(f"Article with id '{article_id}' not found")
        self.article_id = article_id


class VideoNotFoundError(NotFoundError):
    """Raised when a video is not found."""
    
    def __init__(self, video_id: int):
        super().__init__(f"Video with id '{video_id}' not found")
        self.video_id = video_id


class SummarizationError(ExternalServiceError):
    """Raised when article summarization fails."""
    pass


class ChatGenerationError(ExternalServiceError):
    """Raised when AI chat response generation fails."""
    pass


class FeedFetchError(ExternalServiceError):
    """Raised when RSS/YouTube feed fetching fails."""
    
    def __init__(self, feed_url: str, details: Optional[str] = None):
        super().__init__(f"Failed to fetch feed: {feed_url}", details)
        self.feed_url = feed_url


# HTTP exception factories for consistent error responses
def not_found_exception(resource: str, identifier: Union[str, int]) -> HTTPException:
    """Create a 404 Not Found exception."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} with id '{identifier}' not found"
    )


def quota_exceeded_exception(quota_type: str = "daily") -> HTTPException:
    """Create a 429 Too Many Requests exception for quota exceeded."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"{quota_type.capitalize()} message quota exceeded"
    )


def internal_error_exception(message: str = "An internal server error occurred") -> HTTPException:
    """Create a 500 Internal Server Error exception."""
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=message
    )
