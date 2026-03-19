"""
Tiered Feed Service.

Implements the rolling freshness + reservoir strategy for content feeds.
Returns a blend of:
- Tier A (Fresh): published_at within rolling window
- Tier B (Backfill): created_at within backfill window, older publication
- Tier C (Evergreen): older high-quality items

Each item is annotated with:
- freshness_tier: "A" | "B" | "C"
- reason: human-readable explanation
- published_age_seconds: seconds since publication
- added_age_seconds: seconds since ingestion

Includes Redis caching for performance with short TTL.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType, EventType
from app.repositories.user_repo import InteractionEventRepository
from app.services.diversity_mixer import enforce_channel_caps, mix_feed
from app.services.inventory_service import FreshnessTier, Surface, _get_surface_config
from app.services.video_content_policy import apply_content_policy
from app.services.video_hybrid_rerank import rerank_video_candidates
from app.video_age_policy import build_surface_age_filters, make_default_policy
from app.video_surface_rules import (
    effective_content_type,
    surface_content_filter,
    visible_promotion_filter,
)

logger = get_logger(__name__)

# Cache TTL for tiered feed (seconds)
TIERED_FEED_CACHE_TTL = 45  # 45 seconds - balance freshness vs DB load
CONSUMED_SUPPRESSION_HOURS = 24
EXPOSED_DEMOTION_HOURS = 6
ARTICLE_CONSUMED_EVENTS = {
    EventType.OPEN_SOURCE,
    EventType.SHARE,
    EventType.SAVE,
    EventType.CHAT_START,
    EventType.CHAT_MESSAGE,
}
VIDEO_CONSUMED_EVENTS = ARTICLE_CONSUMED_EVENTS | {
    EventType.VIDEO_SAVE,
    EventType.VIDEO_SHARE,
    EventType.VIDEO_50PCT,
    EventType.VIDEO_95PCT,
}
VIDEO_EXPOSED_EVENTS = {EventType.VIDEO_IMPRESSION}


@dataclass
class FeedResponseMeta:
    """Metadata about a feed response for diagnostics."""

    source: str  # "redis" | "db"
    cache_key: Optional[str]
    cache_hit: bool
    generated_at: datetime
    tier_config: Dict[str, int]
    surface: str
    remaining_window_count: int = 0


def _get_redis_client():
    """Get Redis client using shared connection pool."""
    try:
        from app.core.dependencies import get_redis

        return get_redis()
    except Exception as e:
        logger.debug(f"Redis not available for feed cache: {e}")
        return None


def _cache_key(
    surface: Surface,
    limit: int,
    offset: int,
    require_ai: bool,
    hybrid_video_rerank: bool,
    device_id: Optional[str] = None,
) -> str:
    """Generate cache key for tiered feed."""
    base_key = (
        f"blips:tiered_feed:{surface.value}:l{limit}:o{offset}:ai{int(require_ai)}:"
        f"hybrid{int(hybrid_video_rerank)}"
    )
    if not device_id:
        return base_key
    device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
    return f"{base_key}:d{device_hash}"


def invalidate_tiered_feed_cache(surface: Optional[Surface] = None):
    """
    Invalidate tiered feed cache.

    Args:
        surface: Specific surface to invalidate, or None for all
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return

    try:
        if surface:
            pattern = f"blips:tiered_feed:{surface.value}:*"
        else:
            pattern = "blips:tiered_feed:*"

        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
            logger.debug(f"Invalidated {len(keys)} tiered feed cache entries")
    except Exception as e:
        logger.warning(f"Failed to invalidate feed cache: {e}")


@dataclass
class TieredItem:
    """Content item with tier annotation."""

    item: ContentItem
    tier: FreshnessTier
    reason: str
    published_age_seconds: int
    added_age_seconds: int


def _surface_to_content_type(surface: Surface) -> ContentType:
    """Map surface to content type."""
    return {
        Surface.ARTICLES: ContentType.ARTICLE,
        Surface.VIDEOS: ContentType.VIDEO,
        Surface.REELS: ContentType.REEL,
    }[surface]


def _annotate_item(
    item: ContentItem, tier: FreshnessTier, reason: str, now: datetime
) -> TieredItem:
    """Add tier annotation to a content item."""
    pub_age = int((now - item.published_at).total_seconds()) if item.published_at else 0
    add_age = int((now - item.created_at).total_seconds()) if item.created_at else 0

    return TieredItem(
        item=item,
        tier=tier,
        reason=reason,
        published_age_seconds=pub_age,
        added_age_seconds=add_age,
    )


def get_tiered_feed(
    db: Session,
    surface: Surface,
    limit: int = 20,
    offset: int = 0,
    now: Optional[datetime] = None,
    require_ai_processed: bool = True,
    hybrid_video_rerank: bool = False,
    device_id: Optional[str] = None,
) -> Tuple[List[TieredItem], bool, int]:
    """
    Get a tiered blend of content items for a surface.

    Strategy:
    1. Fill from Tier A (fresh published) first
    2. If insufficient, add Tier B (recently added)
    3. If still insufficient, add Tier C (evergreen)
    4. Apply diversity mixing to the combined result

    Args:
        db: Database session
        surface: Which surface to query
        limit: Number of items to return
        offset: Pagination offset
        now: Current time (for testing)
        require_ai_processed: Filter to AI-processed content (default True for articles/videos)

    Returns:
        Tuple of (tiered items, has_more, remaining_window_count)
    """
    now = now or datetime.utcnow()
    cfg = _get_surface_config(surface)
    content_type = _surface_to_content_type(surface)

    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])
    evergreen_cutoff = now - timedelta(days=cfg["evergreen_days"])

    # Base filter
    base_filter = and_(
        surface_content_filter(surface.value),
        visible_promotion_filter(),
        ContentItem.is_suppressed.is_(False),
        ContentItem.curation_status == ContentStatus.PROMOTED,
    )

    if require_ai_processed:
        base_filter = and_(base_filter, ContentItem.ai_processed.is_(True))

    # Defense-in-depth: for REELS, enforce max duration at query level.
    # Some items slip through ingestion classification (e.g. is_short metadata
    # flag without a valid duration).  This prevents multi-hour "reels".
    if surface == Surface.REELS:
        max_dur = settings.REEL_MAX_DURATION_SECONDS
        base_filter = and_(
            base_filter,
            or_(
                ContentItem.duration_seconds.is_(None),  # unknown duration still OK (shorts URLs)
                ContentItem.duration_seconds <= max_dur,  # known duration must be short
            ),
        )

    if surface in (Surface.VIDEOS, Surface.REELS):
        default_policy = make_default_policy(
            fresh_hours=cfg["fresh_hours"],
            backfill_hours=cfg["backfill_hours"],
            evergreen_days=cfg["evergreen_days"],
        )
        age_filters = build_surface_age_filters(now=now, default_policy=default_policy)
        fresh_window_filter = age_filters.fresh
        backfill_window_filter = age_filters.backfill
        evergreen_tier_filter = age_filters.evergreen_tier
    else:
        fresh_window_filter = ContentItem.published_at >= fresh_cutoff
        backfill_window_filter = and_(
            ContentItem.created_at >= backfill_cutoff,
            ContentItem.published_at < fresh_cutoff,
        )
        evergreen_tier_filter = and_(
            ContentItem.published_at < fresh_cutoff,
            ContentItem.published_at >= evergreen_cutoff,
        )

    results: List[TieredItem] = []
    seen_ids = set()

    # Calculate how many we need from each tier
    # We fetch more to allow diversity mixing
    fetch_multiplier = 3
    target_count = limit + offset + 1

    # =========================================================================
    # TIER A: Fresh (published within window)
    # =========================================================================
    total_fresh_count = (
        apply_content_policy(
            db.query(ContentItem).filter(base_filter),
            content_type=content_type,
        )
        .filter(fresh_window_filter)
        .count()
    )
    tier_a_query = apply_content_policy(
        db.query(ContentItem).filter(base_filter),
        content_type=content_type,
    ).filter(fresh_window_filter)
    if surface in (Surface.VIDEOS, Surface.REELS):
        tier_a_query = tier_a_query.order_by(
            desc(ContentItem.published_at),
            desc(ContentItem.promotion_score),
            desc(ContentItem.global_score),
        )
    else:
        tier_a_query = tier_a_query.order_by(
            desc(ContentItem.global_score),
            desc(ContentItem.published_at),
        )
    tier_a_items = tier_a_query.limit(target_count * fetch_multiplier).all()

    for item in tier_a_items:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            results.append(_annotate_item(item, FreshnessTier.A, "fresh_published", now))

    logger.debug(f"Tier A: {len(tier_a_items)} candidates, {len(results)} added")

    # =========================================================================
    # TIER B: Backfill (added recently, published older)
    # =========================================================================
    if len(results) < target_count * fetch_multiplier:
        tier_b_items = (
            apply_content_policy(
                db.query(ContentItem).filter(base_filter),
                content_type=content_type,
            )
            .filter(backfill_window_filter)
            .order_by(
                desc(ContentItem.promotion_score),
                desc(ContentItem.global_score),
                desc(ContentItem.created_at),
            )
            .limit(target_count * fetch_multiplier)
            .all()
        )

        tier_b_added = 0
        for item in tier_b_items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                results.append(_annotate_item(item, FreshnessTier.B, "recently_added", now))
                tier_b_added += 1

        logger.debug(f"Tier B: {len(tier_b_items)} candidates, {tier_b_added} added")

    # =========================================================================
    # TIER C: Evergreen (older high-quality content)
    # =========================================================================
    if len(results) < target_count * fetch_multiplier:
        tier_c_items = (
            apply_content_policy(
                db.query(ContentItem).filter(base_filter),
                content_type=content_type,
            )
            .filter(
                evergreen_tier_filter,
                ContentItem.global_score >= 0.3,  # Quality threshold
            )
            .order_by(
                desc(ContentItem.promotion_score),
                desc(ContentItem.global_score),
                desc(ContentItem.published_at),
            )
            .limit(target_count * fetch_multiplier)
            .all()
        )

        tier_c_added = 0
        for item in tier_c_items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                results.append(_annotate_item(item, FreshnessTier.C, "evergreen", now))
                tier_c_added += 1

        logger.debug(f"Tier C: {len(tier_c_items)} candidates, {tier_c_added} added")

    # =========================================================================
    # Apply diversity mixing to the combined candidates
    # =========================================================================
    # Extract raw items for mixing
    consumed_ids, exposed_ids = _get_recent_feedback_ids(db, device_id, surface)
    if consumed_ids:
        results = [tiered for tiered in results if tiered.item.id not in consumed_ids]
    if exposed_ids:
        promoted = [tiered for tiered in results if tiered.item.id not in exposed_ids]
        demoted = [tiered for tiered in results if tiered.item.id in exposed_ids]
        results = promoted + demoted

    raw_items = [t.item for t in results]

    if surface == Surface.VIDEOS and hybrid_video_rerank:
        raw_items = rerank_video_candidates(raw_items, target_count=target_count)

    # Mix for diversity (this returns a subset in mixed order)
    surface_name = surface.value
    mixed_items = mix_feed(raw_items, surface=surface_name, target_size=target_count)

    # Enforce position-based channel caps (videos/reels only)
    mixed_items = enforce_channel_caps(mixed_items, surface=surface_name)

    # Map back to tiered items
    item_to_tiered = {t.item.id: t for t in results}
    mixed_tiered = [item_to_tiered[item.id] for item in mixed_items if item.id in item_to_tiered]

    # Apply pagination
    paginated = mixed_tiered[offset : offset + limit]
    has_more = len(mixed_tiered) > offset + limit
    served_fresh_count = sum(1 for t in mixed_tiered[: offset + limit] if t.tier == FreshnessTier.A)
    remaining_window_count = max(0, int(total_fresh_count) - int(served_fresh_count))

    logger.info(
        f"Tiered feed for {surface.value}: "
        f"A={sum(1 for t in paginated if t.tier == FreshnessTier.A)} "
        f"B={sum(1 for t in paginated if t.tier == FreshnessTier.B)} "
        f"C={sum(1 for t in paginated if t.tier == FreshnessTier.C)} "
        f"total={len(paginated)}"
    )

    return paginated, has_more, remaining_window_count


def _default_starters_for(item) -> Dict[str, Any]:
    """Generate title-based conversation starters at serving time when DB value is empty."""
    short_title = item.title[:40] + "..." if len(item.title) > 40 else item.title
    if effective_content_type(item) == ContentType.VIDEO:
        starters = [
            f"What are the key takeaways from '{short_title}'?",
            "Can you explain the main concepts?",
            "What practical applications does this have?",
        ]
    else:
        starters = [
            f"What are the implications of '{short_title}'?",
            "Can you break down the key points?",
            "How does this compare to similar developments?",
        ]
    return {
        "starters": starters,
        "fallback": [
            "What are the main points of this?",
            "Can you summarize this for me?",
            "What should I know about this topic?",
        ],
    }


def tiered_item_to_dict(tiered: TieredItem) -> Dict[str, Any]:
    """
    Convert a tiered item to a dictionary with all fields.

    Includes backward-compatible fields plus new tier annotations.
    """
    item = tiered.item
    item_type = effective_content_type(item)
    summary = item.summary or ""
    if not summary and item_type == ContentType.VIDEO:
        description = item.description or ""
        summary = description[:320]

    # Base fields (backward compatible)
    result = {
        "id": item.id,
        "title": item.title,
        "source_url": item.source_url,
        "summary": summary,
        "image_url": item.image_url or None,  # coerce empty string to null
        "source": item.source or "Unknown",
        "created_at": item.created_at.isoformat() if item.created_at else None,
        # New: explicit published_at (not just date)
        "published_at": item.published_at.isoformat() if item.published_at else None,
        # Backward compatible: published_date as date string
        "published_date": item.published_at.date().isoformat() if item.published_at else None,
        # NEW: Tier annotation fields
        "freshness_tier": tiered.tier.value,
        "freshness_reason": tiered.reason,
        "published_age_seconds": tiered.published_age_seconds,
        "added_age_seconds": tiered.added_age_seconds,
        # Ranking metadata — used by personalised re-rank pass; included in
        # the cached payload so rerank_feed() can operate without DB access.
        "global_score": item.global_score or 0.0,
        "promotion_score": item.promotion_score or 0.0,
        "recency_score": item.recency_score or 1.0,
        "topics": item.topics or [],
        "channel_id": item.channel_id,
        "acquisition_lane": item.acquisition_lane,
        "source_status": item.source_status,
        "views_per_hour": item.views_per_hour,
        "format_fit_score": item.format_fit_score,
        "promotion_reason": item.promotion_reason,
        # Conversation starters (inline to avoid separate API call)
        # Serve persisted starters; generate title-based defaults at serving
        # time if ingestion/backfill didn't populate them.
        "conversation_starters": item.conversation_starters
        if item.conversation_starters
        else _default_starters_for(item),
    }

    # Type-specific fields
    if item_type == ContentType.ARTICLE:
        result["read_time_minutes"] = max(1, len(summary) // 200)
        result["tags"] = [{"name": topic} for topic in (item.topics or [])]

    elif item_type in (ContentType.VIDEO, ContentType.REEL):
        result["video_url"] = item.video_url or item.source_url
        result["thumbnail_url"] = item.image_url or None  # coerce empty string
        result["category"] = item.topics[0] if item.topics else "Technology"
        result["duration_seconds"] = item.duration_seconds
        result["hot_score"] = int(item.global_score * 100) if item.global_score else 0

    # Optional extraction debug fields (only when DEBUG_ROUTES_ENABLED)
    if settings.DEBUG_ROUTES_ENABLED:
        result["_debug"] = {
            "canonical_url": getattr(item, "canonical_url", None),
            "has_content_text": bool(getattr(item, "content_text", None)),
        }

    return result


def get_cached_tiered_feed(
    db: Session,
    surface: Surface,
    limit: int = 20,
    offset: int = 0,
    require_ai_processed: bool = True,
    hybrid_video_rerank: bool = False,
    device_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, FeedResponseMeta]:
    """
    Get tiered feed with Redis caching.

    This returns serialized dicts (ready for API response) with caching.
    Cache is invalidated after top-up completion.

    Args:
        db: Database session
        surface: Which surface to query
        limit: Number of items to return
        offset: Pagination offset
        require_ai_processed: Filter to AI-processed content

    Returns:
        Tuple of (list of item dicts, has_more, metadata)
    """
    now = datetime.utcnow()
    cache_key = _cache_key(
        surface,
        limit,
        offset,
        require_ai_processed,
        hybrid_video_rerank,
        device_id,
    )
    redis_client = _get_redis_client()
    cfg = _get_surface_config(surface)

    # Try to get from cache
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                logger.debug(f"Cache HIT for {cache_key}")

                # Log cache hit with item preview for debugging
                items = data["items"]
                if items:
                    newest = max((i.get("published_at", "") for i in items), default="none")
                    logger.info(
                        f"Feed cache HIT: surface={surface.value} key={cache_key} "
                        f"items={len(items)} newest_published={newest}"
                    )

                meta = FeedResponseMeta(
                    source="redis",
                    cache_key=cache_key,
                    cache_hit=True,
                    generated_at=now,
                    tier_config=cfg,
                    surface=surface.value,
                )
                meta.remaining_window_count = int(data.get("remaining_window_count", 0) or 0)
                return data["items"], data["has_more"], meta
        except Exception as e:
            logger.debug(f"Cache read failed: {e}")

    # Cache miss - query database
    logger.debug(f"Cache MISS for {cache_key}")
    tiered_items, has_more, remaining_window_count = get_tiered_feed(
        db,
        surface,
        limit=limit,
        offset=offset,
        require_ai_processed=require_ai_processed,
        hybrid_video_rerank=hybrid_video_rerank,
        device_id=device_id,
    )

    items = [tiered_item_to_dict(t) for t in tiered_items]

    # Log DB query result for debugging
    if items:
        newest = max((i.get("published_at", "") for i in items), default="none")
        tier_dist = {}
        for i in items:
            t = i.get("freshness_tier", "?")
            tier_dist[t] = tier_dist.get(t, 0) + 1
        logger.info(
            f"Feed cache MISS: surface={surface.value} key={cache_key} "
            f"items={len(items)} newest_published={newest} tiers={tier_dist}"
        )

    # Cache the result
    if redis_client:
        try:
            cache_data = json.dumps(
                {
                    "items": items,
                    "has_more": has_more,
                    "remaining_window_count": remaining_window_count,
                }
            )
            redis_client.setex(cache_key, TIERED_FEED_CACHE_TTL, cache_data)
            logger.debug(f"Cached {len(items)} items for {cache_key}")
        except Exception as e:
            logger.debug(f"Cache write failed: {e}")

    meta = FeedResponseMeta(
        source="db",
        cache_key=cache_key,
        cache_hit=False,
        generated_at=now,
        tier_config=cfg,
        surface=surface.value,
        remaining_window_count=remaining_window_count,
    )
    return items, has_more, meta


def _get_recent_feedback_ids(
    db: Session,
    device_id: Optional[str],
    surface: Surface,
) -> Tuple[Set[int], Set[int]]:
    """Return consumed ids and exposed-only ids for device-scoped fresh sessions."""
    if not device_id:
        return set(), set()

    interaction_repo = InteractionEventRepository(db)
    if surface == Surface.ARTICLES:
        consumed_event_types = ARTICLE_CONSUMED_EVENTS
        exposed_event_types: Set[EventType] = set()
    else:
        consumed_event_types = VIDEO_CONSUMED_EVENTS
        exposed_event_types = VIDEO_EXPOSED_EVENTS

    return interaction_repo.get_recent_feedback_ids(
        device_id=device_id,
        consumed_event_types=consumed_event_types,
        consumed_hours=CONSUMED_SUPPRESSION_HOURS,
        exposed_event_types=exposed_event_types,
        exposed_hours=EXPOSED_DEMOTION_HOURS,
    )
