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
    summary = getattr(item, "summary", "") or ""
    canonical_url = getattr(item, "canonical_url", None)
    source_url = getattr(item, "source_url", None)
    image_url = getattr(item, "image_url", None)
    published_at = getattr(item, "published_at", None)
    created_at = getattr(item, "created_at", None)
    topics = getattr(item, "topics", None) or []
    conversation_starters = getattr(item, "conversation_starters", None)
    source = getattr(item, "source", "") or ""

    return {
        "id": item.id,
        "type": ContentType.ARTICLE.value,
        "title": display_article_title(item.title, canonical_url or source_url),
        "source": source,
        "source_url": source_url,
        "summary": summary,
        "image_url": image_url or None,
        "published_date": published_at.date() if published_at else None,
        "published_at": published_at,
        "created_at": created_at,
        "read_time_minutes": max(1, len(summary) // 200) if summary else 1,
        "tags": [{"name": topic} for topic in topics],
        "freshness_tier": freshness["freshness_tier"],
        "freshness_reason": freshness["freshness_reason"],
        "published_age_seconds": freshness["published_age_seconds"],
        "added_age_seconds": freshness["added_age_seconds"],
        "conversation_starters": conversation_starters,
    }


def content_item_to_video_payload(
    item: ContentItem, *, now: datetime | None = None
) -> dict[str, Any]:
    """Convert a content item to a feed-card-compatible video payload."""
    effective_type = effective_content_type(item)
    surface = Surface.REELS if effective_type == ContentType.REEL else Surface.VIDEOS
    freshness = _compute_freshness(item, surface=surface, now=now or datetime.utcnow())
    topics = getattr(item, "topics", None) or []
    summary = getattr(item, "summary", "") or ""
    image_url = getattr(item, "image_url", None)
    video_url = getattr(item, "video_url", None)
    source_url = getattr(item, "source_url", None)
    source = getattr(item, "source", None) or "YouTube"
    duration_seconds = getattr(item, "duration_seconds", None)
    global_score = getattr(item, "global_score", None)
    created_at = getattr(item, "created_at", None)
    published_at = getattr(item, "published_at", None)
    conversation_starters = getattr(item, "conversation_starters", None)

    return {
        "id": item.id,
        "type": effective_type.value,
        "title": item.title,
        "summary": summary,
        "video_url": video_url or source_url,
        "source_url": source_url,
        "thumbnail_url": image_url or None,
        "source": source,
        "category": (topics[0] if topics else "Technology"),
        "duration_seconds": duration_seconds,
        "hot_score": int(global_score * 100) if global_score else 0,
        "created_at": created_at,
        "published_at": published_at,
        "freshness_tier": freshness["freshness_tier"],
        "freshness_reason": freshness["freshness_reason"],
        "published_age_seconds": freshness["published_age_seconds"],
        "added_age_seconds": freshness["added_age_seconds"],
        "conversation_starters": conversation_starters,
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
