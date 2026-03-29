from __future__ import annotations

"""Helpers for runtime curation policy decisions."""

from app.core.config import settings
from app.models.content import ContentStatus, ContentType


def review_queue_target_status(
    *,
    content_type: ContentType | None = None,
    acquisition_lane: str | None = None,
    reel_cap: int | None = None,
) -> ContentStatus:
    """Status assigned to newly-ingested items that would normally enter review."""
    if settings.AUTO_APPROVE_REVIEW_CONTENT:
        return ContentStatus.PROMOTED
    if content_type == ContentType.REEL and bool(getattr(settings, "AUTO_PROMOTE_REELS", True)):
        normalized_lane = (acquisition_lane or "").strip().lower()
        if normalized_lane in {"search", "trending"}:
            return ContentStatus.PROMOTED
        if normalized_lane == "curated" and int(reel_cap or 0) > 0:
            return ContentStatus.PROMOTED
    return ContentStatus.CANDIDATE
