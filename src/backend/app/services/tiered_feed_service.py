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

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from sqlalchemy import desc, and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.services.inventory_service import Surface, FreshnessTier, _get_surface_config
from app.services.diversity_mixer import mix_feed

logger = get_logger(__name__)

# Cache TTL for tiered feed (seconds)
TIERED_FEED_CACHE_TTL = 45  # 45 seconds - balance freshness vs DB load


@dataclass
class FeedResponseMeta:
    """Metadata about a feed response for diagnostics."""
    source: str  # "redis" | "db"
    cache_key: Optional[str]
    cache_hit: bool
    generated_at: datetime
    tier_config: Dict[str, int]
    surface: str


def _get_redis_client():
    """Get Redis client for caching."""
    try:
        import redis
        return redis.from_url(settings.REDIS_URL)
    except Exception as e:
        logger.debug(f"Redis not available for feed cache: {e}")
        return None


def _cache_key(surface: Surface, limit: int, offset: int, require_ai: bool) -> str:
    """Generate cache key for tiered feed."""
    return f"blips:tiered_feed:{surface.value}:l{limit}:o{offset}:ai{int(require_ai)}"


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
    item: ContentItem,
    tier: FreshnessTier,
    reason: str,
    now: datetime
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
) -> Tuple[List[TieredItem], bool]:
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
        Tuple of (tiered items, has_more)
    """
    now = now or datetime.utcnow()
    cfg = _get_surface_config(surface)
    content_type = _surface_to_content_type(surface)
    
    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])
    evergreen_cutoff = now - timedelta(days=cfg["evergreen_days"])
    
    # Base filter
    base_filter = and_(
        ContentItem.type == content_type,
        ContentItem.is_suppressed.is_(False),
    )
    
    if require_ai_processed:
        base_filter = and_(base_filter, ContentItem.ai_processed.is_(True))
    
    results: List[TieredItem] = []
    seen_ids = set()
    
    # Calculate how many we need from each tier
    # We fetch more to allow diversity mixing
    fetch_multiplier = 3
    target_count = limit + offset
    
    # =========================================================================
    # TIER A: Fresh (published within window)
    # =========================================================================
    tier_a_items = db.query(ContentItem).filter(
        base_filter,
        ContentItem.published_at >= fresh_cutoff,
    ).order_by(
        desc(ContentItem.global_score),
        desc(ContentItem.published_at)
    ).limit(target_count * fetch_multiplier).all()
    
    for item in tier_a_items:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            results.append(_annotate_item(item, FreshnessTier.A, "fresh_published", now))
    
    logger.debug(f"Tier A: {len(tier_a_items)} candidates, {len(results)} added")
    
    # =========================================================================
    # TIER B: Backfill (added recently, published older)
    # =========================================================================
    if len(results) < target_count * fetch_multiplier:
        tier_b_items = db.query(ContentItem).filter(
            base_filter,
            ContentItem.created_at >= backfill_cutoff,
            ContentItem.published_at < fresh_cutoff,
        ).order_by(
            desc(ContentItem.global_score),
            desc(ContentItem.created_at)
        ).limit(target_count * fetch_multiplier).all()
        
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
        tier_c_items = db.query(ContentItem).filter(
            base_filter,
            ContentItem.published_at < fresh_cutoff,
            ContentItem.published_at >= evergreen_cutoff,
            ContentItem.global_score >= 0.3,  # Quality threshold
        ).order_by(
            desc(ContentItem.global_score),
            desc(ContentItem.published_at)
        ).limit(target_count * fetch_multiplier).all()
        
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
    raw_items = [t.item for t in results]
    
    # Mix for diversity (this returns a subset in mixed order)
    surface_name = surface.value
    mixed_items = mix_feed(raw_items, surface=surface_name, target_size=target_count)
    
    # Map back to tiered items
    item_to_tiered = {t.item.id: t for t in results}
    mixed_tiered = [item_to_tiered[item.id] for item in mixed_items if item.id in item_to_tiered]
    
    # Apply pagination
    paginated = mixed_tiered[offset:offset + limit]
    has_more = len(mixed_tiered) > offset + limit
    
    logger.info(
        f"Tiered feed for {surface.value}: "
        f"A={sum(1 for t in paginated if t.tier == FreshnessTier.A)} "
        f"B={sum(1 for t in paginated if t.tier == FreshnessTier.B)} "
        f"C={sum(1 for t in paginated if t.tier == FreshnessTier.C)} "
        f"total={len(paginated)}"
    )
    
    return paginated, has_more


def tiered_item_to_dict(tiered: TieredItem) -> Dict[str, Any]:
    """
    Convert a tiered item to a dictionary with all fields.
    
    Includes backward-compatible fields plus new tier annotations.
    """
    item = tiered.item
    
    # Base fields (backward compatible)
    result = {
        "id": item.id,
        "title": item.title,
        "source_url": item.source_url,
        "summary": item.summary or "",
        "image_url": item.image_url,
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
        
        # Conversation starters (inline to avoid separate API call)
        "conversation_starters": item.conversation_starters or {},
    }
    
    # Type-specific fields
    if item.type == ContentType.ARTICLE:
        result["read_time_minutes"] = max(1, len(item.summary or "") // 200)
        result["tags"] = [{"name": topic} for topic in (item.topics or [])]
    
    elif item.type in (ContentType.VIDEO, ContentType.REEL):
        result["video_url"] = item.video_url or item.source_url
        result["thumbnail_url"] = item.image_url
        result["category"] = item.topics[0] if item.topics else "Technology"
        result["duration_seconds"] = item.duration_seconds
        result["hot_score"] = int(item.global_score * 100) if item.global_score else 0
    
    return result


def get_cached_tiered_feed(
    db: Session,
    surface: Surface,
    limit: int = 20,
    offset: int = 0,
    require_ai_processed: bool = True,
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
    cache_key = _cache_key(surface, limit, offset, require_ai_processed)
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
                return data["items"], data["has_more"], meta
        except Exception as e:
            logger.debug(f"Cache read failed: {e}")
    
    # Cache miss - query database
    logger.debug(f"Cache MISS for {cache_key}")
    tiered_items, has_more = get_tiered_feed(
        db,
        surface,
        limit=limit,
        offset=offset,
        require_ai_processed=require_ai_processed,
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
            cache_data = json.dumps({"items": items, "has_more": has_more})
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
    )
    return items, has_more, meta

