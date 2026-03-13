"""Shared policy helpers for curated-only video/reel mode."""

from __future__ import annotations

from sqlalchemy import and_, or_

from app.core.config import settings
from app.models.content import ContentItem, ContentType


def curated_only_mode_enabled() -> bool:
    """Return True when feeds should only use curated channel content."""
    return bool(settings.YOUTUBE_CURATED_ONLY)


def youtube_discovery_enabled() -> bool:
    """Return True only when the discovery lane is explicitly allowed."""
    return bool(settings.YOUTUBE_DISCOVERY_ENABLED) and not curated_only_mode_enabled()


def curated_lane_clause():
    """SQLAlchemy predicate that keeps only curated video/reel items."""
    return or_(ContentItem.acquisition_lane.is_(None), ContentItem.acquisition_lane == "curated")


def apply_content_policy(query, *, content_type: ContentType | None = None):
    """Apply curated-only filtering to queries when needed."""
    if not curated_only_mode_enabled():
        return query

    if content_type in (ContentType.VIDEO, ContentType.REEL):
        return query.filter(curated_lane_clause())

    if content_type == ContentType.ARTICLE:
        return query

    return query.filter(
        or_(
            ContentItem.type == ContentType.ARTICLE,
            and_(
                ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
                curated_lane_clause(),
            ),
        )
    )
