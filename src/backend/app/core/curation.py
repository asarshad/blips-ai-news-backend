"""Helpers for runtime curation policy decisions."""

from app.core.config import settings
from app.models.content import ContentStatus


def review_queue_target_status() -> ContentStatus:
    """Status assigned to newly-ingested items that would normally enter review."""
    if settings.AUTO_APPROVE_REVIEW_CONTENT:
        return ContentStatus.PROMOTED
    return ContentStatus.CANDIDATE
