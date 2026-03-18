"""Shared rules for assigning video items to videos vs reels surfaces."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, not_, or_

from app.models.content import ContentItem, ContentType


def has_explicit_shorts_url(url: str | None) -> bool:
    """Return True when a URL is an explicit YouTube Shorts permalink."""
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return "youtube.com/shorts/" in lowered


def item_has_explicit_shorts_url(item: Any) -> bool:
    """Check persisted URL fields for an explicit Shorts permalink."""
    for candidate in (
        getattr(item, "video_url", None),
        getattr(item, "source_url", None),
        getattr(item, "canonical_url", None),
    ):
        if has_explicit_shorts_url(candidate):
            return True
    return False


def effective_content_type(item: Any):
    """Treat persisted VIDEO rows with explicit Shorts URLs as reels at read time."""
    item_type = getattr(item, "type", None)
    if item_type == ContentType.VIDEO and item_has_explicit_shorts_url(item):
        return ContentType.REEL
    return item_type


def surface_content_filter(surface_name: str):
    """Build a SQL clause that maps misclassified Shorts rows to the reels surface."""
    shorts_clause = or_(
        ContentItem.video_url.ilike("%youtube.com/shorts/%"),
        ContentItem.source_url.ilike("%youtube.com/shorts/%"),
        ContentItem.canonical_url.ilike("%youtube.com/shorts/%"),
    )

    if surface_name == "videos":
        return and_(ContentItem.type == ContentType.VIDEO, not_(shorts_clause))

    if surface_name == "reels":
        return or_(
            ContentItem.type == ContentType.REEL,
            and_(ContentItem.type == ContentType.VIDEO, shorts_clause),
        )

    return ContentItem.type == ContentType.ARTICLE
