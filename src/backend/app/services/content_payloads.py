"""Helpers for shaping single-item article/video payloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.article_hydration import display_article_title
from app.models.content import ContentItem, ContentType
from app.services.inventory_service import FreshnessTier, Surface, _get_surface_config
from app.video_surface_rules import effective_content_type


def effective_content_type_value(item: ContentItem) -> str:
    """Return the effective ARTICLE/VIDEO/REEL type string for an item."""
    return effective_content_type(item).value


def effective_notification_surface(item: ContentItem) -> str:
    """Return the client surface name for an item."""
    match effective_content_type(item):
        case ContentType.ARTICLE:
            return "articles"
        case ContentType.VIDEO:
            return "videos"
        case ContentType.REEL:
            return "reels"
    return "articles"


def content_item_to_article_payload(
    item: ContentItem, *, now: datetime | None = None
) -> dict[str, Any]:
    """Convert a content item to a feed-card-compatible article payload."""
    freshness = _compute_freshness(
        item,
        surface=Surface.ARTICLES,
        now=now or datetime.utcnow(),
    )
    summary = item.summary or ""

    return {
        "id": item.id,
        "type": ContentType.ARTICLE.value,
        "title": display_article_title(item.title, item.canonical_url or item.source_url),
        "source": item.source or "",
        "source_url": item.source_url,
        "summary": summary,
        "image_url": item.image_url or None,
        "published_date": item.published_at.date() if item.published_at else None,
        "published_at": item.published_at,
        "created_at": item.created_at,
        "read_time_minutes": max(1, len(summary) // 200) if summary else 1,
        "tags": [{"name": topic} for topic in (item.topics or [])],
        "freshness_tier": freshness["freshness_tier"],
        "freshness_reason": freshness["freshness_reason"],
        "published_age_seconds": freshness["published_age_seconds"],
        "added_age_seconds": freshness["added_age_seconds"],
        "conversation_starters": item.conversation_starters,
    }


def content_item_to_video_payload(
    item: ContentItem, *, now: datetime | None = None
) -> dict[str, Any]:
    """Convert a content item to a feed-card-compatible video payload."""
    effective_type = effective_content_type(item)
    surface = Surface.REELS if effective_type == ContentType.REEL else Surface.VIDEOS
    freshness = _compute_freshness(item, surface=surface, now=now or datetime.utcnow())

    return {
        "id": item.id,
        "type": effective_type.value,
        "title": item.title,
        "summary": item.summary or "",
        "video_url": item.video_url or item.source_url,
        "source_url": item.source_url,
        "thumbnail_url": item.image_url or None,
        "source": item.source or "YouTube",
        "category": (item.topics[0] if item.topics else "Technology"),
        "duration_seconds": item.duration_seconds,
        "hot_score": int(item.global_score * 100) if item.global_score else 0,
        "created_at": item.created_at,
        "published_at": item.published_at,
        "freshness_tier": freshness["freshness_tier"],
        "freshness_reason": freshness["freshness_reason"],
        "published_age_seconds": freshness["published_age_seconds"],
        "added_age_seconds": freshness["added_age_seconds"],
        "conversation_starters": item.conversation_starters,
    }


def _compute_freshness(
    item: ContentItem,
    *,
    surface: Surface,
    now: datetime,
) -> dict[str, Any]:
    cfg = _get_surface_config(surface)
    published_at = item.published_at or item.created_at or now
    created_at = item.created_at or published_at
    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])

    if published_at >= fresh_cutoff:
        tier = FreshnessTier.A
        reason = "fresh_published"
    elif created_at >= backfill_cutoff:
        tier = FreshnessTier.B
        reason = "recently_added"
    else:
        tier = FreshnessTier.C
        reason = "evergreen"

    return {
        "freshness_tier": tier.value,
        "freshness_reason": reason,
        "published_age_seconds": max(0, int((now - published_at).total_seconds())),
        "added_age_seconds": max(0, int((now - created_at).total_seconds())),
    }
