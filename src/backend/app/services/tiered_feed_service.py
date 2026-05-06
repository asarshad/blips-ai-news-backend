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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from app.article_hydration import display_article_title
from app.core.config import settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.repositories.user_repo import InteractionEventRepository
from app.services.content_readiness import ready_content_filter
from app.services.diversity_mixer import enforce_channel_caps, mix_feed
from app.services.feed_freshness_strategies import (
    CURRENT_STRATEGY,
    FeedFreshnessStrategy,
    feed_freshness_strategies,
)
from app.services.inventory_service import (
    FreshnessTier,
    Surface,
    _get_surface_config,
    evergreen_min_global_score,
)
from app.services.video_content_policy import apply_content_policy
from app.services.video_duration_hydration import hydrate_missing_video_durations
from app.services.video_hybrid_rerank import rerank_video_candidates
from app.video_age_policy import build_surface_age_filters, make_default_policy
from app.video_surface_rules import effective_content_type

logger = get_logger(__name__)

# Cache TTL for tiered feed (seconds)
TIERED_FEED_CACHE_TTL = 45  # 45 seconds - balance freshness vs DB load
TIERED_FEED_ORDER_VERSION = "v3local_day_score"
FEED_DAY_TIMEZONE = ZoneInfo("America/Vancouver")
CONSUMED_SUPPRESSION_HOURS = 24
EXPOSED_DEMOTION_HOURS = 6
NEGATIVE_ITEM_SUPPRESSION_HOURS = 24
NEGATIVE_CREATOR_SUPPRESSION_HOURS = 168


def _article_day_first_order_clauses():
    """Keep article candidate selection aligned with day-first serving."""
    return (
        desc(func.date(ContentItem.published_at)),
        desc(ContentItem.promotion_score),
        desc(ContentItem.global_score),
        desc(ContentItem.published_at),
    )


def _published_at_timestamp(item: ContentItem) -> float:
    published_at = getattr(item, "published_at", None)
    if not isinstance(published_at, datetime):
        return float("-inf")
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at.timestamp()


def _numeric_score(value: Any) -> float:
    if value is None:
        return float("-inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _day_score_order_key(item: ContentItem) -> Tuple[int, float, float, float]:
    """Serve by published day first, then score within the day."""
    published_at = getattr(item, "published_at", None)
    if isinstance(published_at, datetime):
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        day = published_at.astimezone(FEED_DAY_TIMEZONE).date().toordinal()
    else:
        day = 0
    return (
        day,
        _numeric_score(getattr(item, "promotion_score", None)),
        _numeric_score(getattr(item, "global_score", None)),
        _published_at_timestamp(item),
    )


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
    strategy_name: str = CURRENT_STRATEGY
    strategy_source: Optional[str] = None
    resume_continuity_window_minutes: Optional[int] = None
    resume_snapshot_after_remote_window: bool = True


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
    hybrid_video_rerank: bool,
    strategy_name: str = CURRENT_STRATEGY,
    device_id: Optional[str] = None,
) -> str:
    """Generate cache key for tiered feed."""
    base_key = (
        f"blips:tiered_feed:{surface.value}:s{strategy_name}:l{limit}:o{offset}:"
        f"ov{TIERED_FEED_ORDER_VERSION}:hybrid{int(hybrid_video_rerank)}"
    )
    if surface == Surface.ARTICLES or not device_id:
        return base_key
    device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
    return f"{base_key}:d{device_hash}"


def invalidate_tiered_feed_cache(
    surface: Optional[Surface] = None,
    *,
    device_id: Optional[str] = None,
):
    """
    Invalidate tiered feed cache.

    Args:
        surface: Specific surface to invalidate, or None for all
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return

    try:
        if surface and device_id:
            device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
            pattern = f"blips:tiered_feed:{surface.value}:*:d{device_hash}"
        elif surface:
            pattern = f"blips:tiered_feed:{surface.value}:*"
        elif device_id:
            device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
            pattern = f"blips:tiered_feed:*:d{device_hash}"
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


def _prioritize_unseen_items(
    primary_items: List[ContentItem],
    demoted_items: List[ContentItem],
    *,
    target_size: int,
) -> List[ContentItem]:
    """Use recently exposed items only after unseen items are exhausted."""
    if len(primary_items) >= target_size:
        return primary_items[:target_size]
    remaining = max(0, target_size - len(primary_items))
    return [*primary_items, *demoted_items[:remaining]]


def _feedback_creator_key(item: ContentItem) -> str:
    return (
        str(getattr(item, "channel_id", "") or "").strip().lower()
        or str(getattr(item, "source", "") or "").strip().lower()
        or "unknown"
    )


def _apply_inventory_suppression(
    query,
    *,
    consumed_ids: Set[int],
    negative_item_ids: Set[int],
    negative_creator_keys: Set[str],
):
    if consumed_ids:
        query = query.filter(~ContentItem.id.in_(consumed_ids))
    if negative_item_ids:
        query = query.filter(~ContentItem.id.in_(negative_item_ids))
    if negative_creator_keys:
        creator_key_expr = func.lower(func.coalesce(ContentItem.channel_id, ContentItem.source))
        query = query.filter(~creator_key_expr.in_(sorted(negative_creator_keys)))
    return query


def _count_accessible_fresh_items(
    query,
    *,
    fresh_window_filter,
):
    return query.filter(fresh_window_filter).count()


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
    hybrid_video_rerank: bool = False,
    device_id: Optional[str] = None,
    strategy: Optional[FeedFreshnessStrategy] = None,
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
    Returns:
        Tuple of (tiered items, has_more, remaining_window_count)
    """
    now = now or datetime.utcnow()
    cfg = _get_surface_config(surface)
    content_type = _surface_to_content_type(surface)
    strategy = strategy or feed_freshness_strategies.resolve(surface)
    personalized_device_id = None if surface == Surface.ARTICLES else device_id

    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])
    evergreen_cutoff = now - timedelta(days=cfg["evergreen_days"])
    article_recent_pool_cutoff = now - timedelta(days=settings.ARTICLE_RECENT_POOL_DAYS)

    # Base filter
    base_filter = ready_content_filter(surface.value)

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
    strategy = strategy or feed_freshness_strategies.resolve(surface)
    consumed_ids, exposed_ids = _get_recent_feedback_ids(
        db,
        personalized_device_id,
        surface,
        strategy=strategy,
    )
    negative_item_ids, negative_creator_keys = _get_recent_negative_feedback(
        db, personalized_device_id, surface
    )
    base_inventory_query = apply_content_policy(
        db.query(ContentItem).filter(base_filter),
        content_type=content_type,
    )
    eligible_inventory_query = _apply_inventory_suppression(
        base_inventory_query,
        consumed_ids=consumed_ids,
        negative_item_ids=negative_item_ids,
        negative_creator_keys=negative_creator_keys,
    )

    # Calculate how many we need from each tier
    # We fetch more to allow diversity mixing
    fetch_multiplier = 3
    target_count = limit + offset + 1

    # =========================================================================
    # TIER A: Fresh (published within window)
    # =========================================================================
    total_fresh_count = _count_accessible_fresh_items(
        eligible_inventory_query,
        fresh_window_filter=fresh_window_filter,
    )
    tier_a_query = eligible_inventory_query.filter(fresh_window_filter).order_by(
        *strategy.tier_a_order_clauses(surface=surface, offset=offset)
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
            eligible_inventory_query.filter(backfill_window_filter)
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
        evergreen_min_score = evergreen_min_global_score(surface)
        tier_c_added = 0
        tier_c_candidates = 0

        if surface == Surface.ARTICLES:
            recent_article_items = (
                eligible_inventory_query.filter(
                    ContentItem.published_at < fresh_cutoff,
                    ContentItem.published_at >= article_recent_pool_cutoff,
                )
                .order_by(*_article_day_first_order_clauses())
                .limit(target_count * fetch_multiplier)
                .all()
            )
            tier_c_candidates += len(recent_article_items)
            for item in recent_article_items:
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    results.append(_annotate_item(item, FreshnessTier.C, "recent_archive", now))
                    tier_c_added += 1

            if len(results) < target_count * fetch_multiplier:
                older_evergreen_items = (
                    eligible_inventory_query.filter(
                        ContentItem.published_at < article_recent_pool_cutoff,
                        ContentItem.published_at >= evergreen_cutoff,
                        ContentItem.global_score >= evergreen_min_score,
                    )
                    .order_by(
                        desc(ContentItem.promotion_score),
                        desc(ContentItem.global_score),
                        desc(ContentItem.published_at),
                    )
                    .limit(target_count * fetch_multiplier)
                    .all()
                )
                tier_c_candidates += len(older_evergreen_items)
                for item in older_evergreen_items:
                    if item.id not in seen_ids:
                        seen_ids.add(item.id)
                        results.append(_annotate_item(item, FreshnessTier.C, "evergreen", now))
                        tier_c_added += 1
        else:
            tier_c_items = (
                eligible_inventory_query.filter(
                    evergreen_tier_filter,
                    ContentItem.global_score >= evergreen_min_score,
                )
                .order_by(
                    desc(ContentItem.promotion_score),
                    desc(ContentItem.global_score),
                    desc(ContentItem.published_at),
                )
                .limit(target_count * fetch_multiplier)
                .all()
            )

            tier_c_candidates = len(tier_c_items)
            for item in tier_c_items:
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    results.append(_annotate_item(item, FreshnessTier.C, "evergreen", now))
                    tier_c_added += 1

        logger.debug(f"Tier C: {tier_c_candidates} candidates, {tier_c_added} added")

    # =========================================================================
    # Apply diversity mixing to the combined candidates
    # =========================================================================
    # Extract raw items for mixing
    if consumed_ids:
        results = [tiered for tiered in results if tiered.item.id not in consumed_ids]
    if negative_item_ids or negative_creator_keys:
        results = [
            tiered
            for tiered in results
            if tiered.item.id not in negative_item_ids
            and _feedback_creator_key(tiered.item) not in negative_creator_keys
        ]
    primary_results = [tiered for tiered in results if tiered.item.id not in exposed_ids]
    demoted_results = [tiered for tiered in results if tiered.item.id in exposed_ids]

    primary_items = [tiered.item for tiered in primary_results]
    demoted_items = [tiered.item for tiered in demoted_results]

    if surface in (Surface.VIDEOS, Surface.REELS) and hybrid_video_rerank:
        primary_items = rerank_video_candidates(
            primary_items,
            target_count=target_count,
            surface=surface.value,
        )

    # Mix for diversity (this returns a subset in mixed order)
    surface_name = surface.value
    mixed_primary_items = mix_feed(primary_items, surface=surface_name, target_size=target_count)

    mixed_demoted_items: List[ContentItem] = []
    if len(mixed_primary_items) < target_count and demoted_items:
        remaining = target_count - len(mixed_primary_items)
        if surface in (Surface.VIDEOS, Surface.REELS) and hybrid_video_rerank:
            demoted_items = rerank_video_candidates(
                demoted_items,
                target_count=remaining,
                surface=surface.value,
            )
        mixed_demoted_items = mix_feed(
            demoted_items,
            surface=surface_name,
            target_size=remaining,
        )

    mixed_items = _prioritize_unseen_items(
        mixed_primary_items,
        mixed_demoted_items,
        target_size=target_count,
    )

    # Enforce position-based channel caps (videos/reels only)
    mixed_items = enforce_channel_caps(mixed_items, surface=surface_name)

    if surface == Surface.ARTICLES:
        # Restore the article ranking contract after diversity mixing.
        # mix_feed() re-orders items for source/topic diversity without date
        # awareness, so the final article serving pass must put newer days first
        # and score within each day. Videos/reels keep their reranker order.
        mixed_items.sort(key=_day_score_order_key, reverse=True)

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


def tiered_item_to_dict(
    tiered: TieredItem,
    *,
    duration_overrides: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """
    Convert a tiered item to a dictionary with all fields.

    Includes backward-compatible fields plus new tier annotations.
    """
    item = tiered.item
    item_type = effective_content_type(item)
    duration_seconds = (
        duration_overrides.get(item.id)
        if duration_overrides and item.id in duration_overrides
        else item.duration_seconds
    )
    summary = item.summary or ""

    # Base fields (backward compatible)
    result = {
        "id": item.id,
        "title": display_article_title(
            item.title,
            getattr(item, "canonical_url", None) or item.source_url,
        )
        if item_type == ContentType.ARTICLE
        else item.title,
        "source_url": item.source_url,
        "summary": summary,
        "image_url": item.image_url or None,  # coerce empty string to null
        "source": item.source or "Unknown",
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": getattr(item, "updated_at", None).isoformat()
        if getattr(item, "updated_at", None)
        else None,
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
        result["duration_seconds"] = duration_seconds
        result["hot_score"] = int(item.global_score * 100) if item.global_score else 0

    # Optional extraction debug fields (only when DEBUG_ROUTES_ENABLED)
    if settings.DEBUG_ROUTES_ENABLED:
        result["_debug"] = {
            "canonical_url": getattr(item, "canonical_url", None),
            "has_content_text": bool(getattr(item, "content_text", None)),
        }

    return result


def _normalize_cached_article_titles(
    items: List[Dict[str, Any]],
    surface: Surface,
) -> List[Dict[str, Any]]:
    """Patch cached article payloads so pending placeholders do not leak into the UI."""
    if surface != Surface.ARTICLES:
        return items

    normalized: List[Dict[str, Any]] = []
    mutated = False
    for item in items:
        current_title = item.get("title")
        if not current_title:
            normalized.append(item)
            continue
        display_title = display_article_title(current_title, item.get("source_url"))
        if display_title != current_title:
            updated = dict(item)
            updated["title"] = display_title
            normalized.append(updated)
            mutated = True
        else:
            normalized.append(item)
    return normalized if mutated else items


def _cached_generated_at(payload: Dict[str, Any], fallback: datetime) -> datetime:
    """Recover the original cache generation time for consistent freshness headers."""
    raw = payload.get("generated_at")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return fallback
    return fallback


def get_cached_tiered_feed(
    db: Session,
    surface: Surface,
    limit: int = 20,
    offset: int = 0,
    hybrid_video_rerank: bool = False,
    device_id: Optional[str] = None,
    strategy: Optional[FeedFreshnessStrategy] = None,
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
    Returns:
        Tuple of (list of item dicts, has_more, metadata)
    """
    now = datetime.utcnow()
    strategy = strategy or feed_freshness_strategies.resolve(surface)
    personalized_device_id = None if surface == Surface.ARTICLES else device_id
    strategy_source = (
        feed_freshness_strategies.strategy_source(surface)
        if strategy is not None and strategy.name == feed_freshness_strategies.strategy_name(surface)
        else "session_snapshot"
    )
    cache_key = _cache_key(
        surface,
        limit,
        offset,
        hybrid_video_rerank,
        strategy.name,
        personalized_device_id,
    )
    redis_client = _get_redis_client()
    cfg = _get_surface_config(surface)

    # Try to get from cache
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                generated_at = _cached_generated_at(data, now)
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
                    generated_at=generated_at,
                    tier_config=cfg,
                    surface=surface.value,
                    strategy_name=str(data.get("freshness_strategy") or strategy.name),
                    strategy_source=str(data.get("freshness_strategy_source") or strategy_source),
                    resume_continuity_window_minutes=data.get(
                        "resume_continuity_window_minutes"
                    ),
                    resume_snapshot_after_remote_window=bool(
                        data.get("resume_snapshot_after_remote_window", True)
                    ),
                )
                meta.remaining_window_count = int(data.get("remaining_window_count", 0) or 0)
                return (
                    _normalize_cached_article_titles(data["items"], surface),
                    data["has_more"],
                    meta,
                )
        except Exception as e:
            logger.debug(f"Cache read failed: {e}")

    # Cache miss - query database
    logger.debug(f"Cache MISS for {cache_key}")
    tiered_items, has_more, remaining_window_count = get_tiered_feed(
        db,
        surface,
        limit=limit,
        offset=offset,
        hybrid_video_rerank=hybrid_video_rerank,
        device_id=personalized_device_id,
        strategy=strategy,
    )

    duration_overrides: Dict[int, int] = {}
    if surface in (Surface.VIDEOS, Surface.REELS) and tiered_items:
        from app.repositories.content_repo import ContentItemRepository

        duration_overrides = hydrate_missing_video_durations(
            [tiered.item for tiered in tiered_items],
            content_repo=ContentItemRepository(db),
        )

    items = [tiered_item_to_dict(t, duration_overrides=duration_overrides) for t in tiered_items]

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
                    "generated_at": now.isoformat(),
                    "freshness_strategy": strategy.name,
                    "freshness_strategy_source": strategy_source,
                    "resume_continuity_window_minutes": strategy.resume_continuity_window_minutes(
                        surface=surface
                    ),
                    "resume_snapshot_after_remote_window": strategy.resume_snapshot_after_remote_window(
                        surface=surface
                    ),
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
        strategy_name=strategy.name,
        strategy_source=strategy_source,
        resume_continuity_window_minutes=strategy.resume_continuity_window_minutes(
            surface=surface
        ),
        resume_snapshot_after_remote_window=strategy.resume_snapshot_after_remote_window(
            surface=surface
        ),
    )
    return items, has_more, meta


def _get_recent_feedback_ids(
    db: Session,
    device_id: Optional[str],
    surface: Surface,
    *,
    strategy: Optional[FeedFreshnessStrategy] = None,
) -> Tuple[Set[int], Set[int]]:
    """Return consumed ids and exposed-only ids for device-scoped fresh sessions."""
    if not device_id:
        return set(), set()

    interaction_repo = InteractionEventRepository(db)
    signals = (strategy or feed_freshness_strategies.resolve(surface)).feedback_signals(
        surface=surface
    )

    return interaction_repo.get_recent_feedback_ids(
        device_id=device_id,
        consumed_event_types=signals.consumed_event_types,
        consumed_hours=CONSUMED_SUPPRESSION_HOURS,
        exposed_event_types=signals.exposed_event_types,
        exposed_hours=EXPOSED_DEMOTION_HOURS,
    )


def _get_recent_negative_feedback(
    db: Session,
    device_id: Optional[str],
    surface: Surface,
) -> Tuple[Set[int], Set[str]]:
    """Return recently skipped item ids and creator-downvote keys."""
    if not device_id or surface == Surface.ARTICLES:
        return set(), set()

    interaction_repo = InteractionEventRepository(db)
    return interaction_repo.get_recent_negative_feedback(
        device_id=device_id,
        item_hours=NEGATIVE_ITEM_SUPPRESSION_HOURS,
        creator_hours=NEGATIVE_CREATOR_SUPPRESSION_HOURS,
        content_types=(ContentType.VIDEO, ContentType.REEL),
    )
