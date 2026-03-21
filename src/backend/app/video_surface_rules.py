"""Shared rules for assigning video items to videos vs reels surfaces."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, not_, or_

from app.core.config import settings
from app.integrations.youtube_channels import ContentFormat, get_enabled_channels
from app.models.content import ContentItem, ContentType

_SHORTS_CHANNEL_IDS = frozenset(
    channel.channel_id
    for channel in get_enabled_channels()
    if channel.content_format == ContentFormat.SHORTS
)


def has_explicit_shorts_url(url: str | None) -> bool:
    """Return True when a URL is an explicit YouTube Shorts permalink."""
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return "youtube.com/shorts/" in lowered


def has_shorts_hashtag(text: str | None) -> bool:
    """Return True when the title contains an explicit Shorts hashtag."""
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return "#shorts" in lowered or "#short" in lowered


def channel_is_shorts_native(channel_id: str | None) -> bool:
    """Return True when the channel is configured as Shorts-only."""
    return isinstance(channel_id, str) and channel_id in _SHORTS_CHANNEL_IDS


def item_has_reel_signal(item: Any, *, allow_is_short_hint: bool = False) -> bool:
    """Check whether an entry has a durable Shorts/reel signal."""
    if item_has_explicit_shorts_url(item):
        return True
    if has_shorts_hashtag(getattr(item, "title", None)):
        return True
    if getattr(item, "content_format", None) == ContentFormat.SHORTS:
        return True
    if channel_is_shorts_native(getattr(item, "channel_id", None)):
        return True
    return allow_is_short_hint and bool(getattr(item, "is_short", False))


def classify_video_like_item(item: Any, *, allow_is_short_hint: bool = False):
    """Map a video-like item to VIDEO or REEL using durable shorts signals."""
    item_type = getattr(item, "type", None)
    if item_type == ContentType.ARTICLE:
        return item_type

    duration_seconds = getattr(item, "duration_seconds", None)
    if isinstance(duration_seconds, (int, float)):
        if duration_seconds > settings.REEL_MAX_DURATION_SECONDS:
            return ContentType.VIDEO

    if item_has_reel_signal(item, allow_is_short_hint=allow_is_short_hint):
        return ContentType.REEL
    return ContentType.VIDEO


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
    """Treat persisted video-like rows according to durable Shorts signals."""
    return classify_video_like_item(item)


def _reel_signal_clause():
    shorts_url_clause = or_(
        ContentItem.video_url.ilike("%youtube.com/shorts/%"),
        ContentItem.source_url.ilike("%youtube.com/shorts/%"),
        ContentItem.canonical_url.ilike("%youtube.com/shorts/%"),
    )
    shorts_hashtag_clause = or_(
        ContentItem.title.ilike("%#shorts%"),
        ContentItem.title.ilike("%#short%"),
    )
    clauses = [shorts_url_clause, shorts_hashtag_clause]
    if _SHORTS_CHANNEL_IDS:
        clauses.append(
            and_(
                ContentItem.channel_id.is_not(None),
                ContentItem.channel_id.in_(tuple(_SHORTS_CHANNEL_IDS)),
            )
        )
    return or_(*clauses)


def surface_content_filter(surface_name: str):
    """Build a SQL clause that maps video-like rows to the correct surface."""
    reel_signal_clause = _reel_signal_clause()
    video_like_clause = ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL])
    long_duration_clause = ContentItem.duration_seconds > settings.REEL_MAX_DURATION_SECONDS
    reel_duration_clause = or_(
        ContentItem.duration_seconds.is_(None),
        ContentItem.duration_seconds <= settings.REEL_MAX_DURATION_SECONDS,
    )

    if surface_name == "videos":
        return and_(video_like_clause, or_(long_duration_clause, not_(reel_signal_clause)))

    if surface_name == "reels":
        return and_(video_like_clause, reel_signal_clause, reel_duration_clause)

    return ContentItem.type == ContentType.ARTICLE


def visible_promotion_filter():
    """Exclude promoted items that were later flagged as blocked during rescoring."""
    return or_(
        ContentItem.promotion_reason.is_(None),
        not_(ContentItem.promotion_reason.like("%|blocked=%")),
    )
