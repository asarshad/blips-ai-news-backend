"""Video routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes tiered freshness strategy (A/B/C) and diversity mixing.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.feed_headers import FeedMetadata, compute_feed_version
from app.core.config import settings
from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.core.feature_flags import FeatureFlags, get_feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content import ContentType
from app.repositories.content_repo import ContentItemRepository
from app.schemas.video import Video as VideoSchema
from app.services.ad_mixer import inject_ads
from app.services.content_payloads import content_item_to_video_payload
from app.services.freshness_metrics_service import record_feed_served
from app.services.inventory_service import Surface
from app.services.tiered_feed_service import get_cached_tiered_feed
from app.services.topup_service import check_and_trigger_topup

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


def _content_item_to_video_schema(item) -> dict:
    """Convert ContentItem to Video schema format (backward compatible)."""
    return content_item_to_video_payload(item)


def _cursor_to_offset(cursor: Optional[str], limit: int, page: Optional[int]) -> int:
    """Translate an opaque cursor into the internal offset used by caching."""
    if cursor:
        try:
            return max(0, int(cursor))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    if page and page > 1:
        return (page - 1) * limit
    return 0


VIDEOS_WINDOW_DAYS = max(1, settings.VIDEOS_FRESH_PUBLISHED_HOURS // 24)
REELS_WINDOW_DAYS = max(1, settings.REELS_FRESH_PUBLISHED_HOURS // 24)


def _inventory_state(*, item_count: int, remaining_count: int, offset: int) -> str:
    """Surface-aware feed state for clients and admin diagnostics."""
    if item_count == 0:
        return "caught_up" if offset > 0 and remaining_count == 0 else "warming_up"
    if remaining_count == 0:
        return "caught_up"
    return "healthy"


@router.get("/recent", response_model=Dict[str, Any])
def get_recent_videos(
    limit: int = Query(10, ge=1, le=50, description="Number of videos to return"),
    cursor: Optional[str] = Query(None, description="Cursor returned by the previous page"),
    page: Optional[int] = Query(None, ge=1, include_in_schema=False),
    x_device_id: Optional[str] = Header(None, description="Optional device identifier"),
    response: Response = None,
    db: Session = Depends(get_db),
    flags: FeatureFlags = Depends(get_feature_flags),
):
    """
    Get the most recent videos using tiered freshness strategy.

    Returns a blend of:
    - Tier A (Fresh): videos published within the rolling 72-hour window
    - Tier B (Backfill): videos added recently but published earlier
    - Tier C (Evergreen): older high-quality videos

    Each video includes freshness_tier, published_age_seconds, and added_age_seconds.
    Results are diversity-mixed to ensure varied source distribution.
    """
    # Check videos feature flag
    if not flags.is_enabled("videos"):
        raise HTTPException(status_code=503, detail="Videos feature is currently disabled")

    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)

    offset = _cursor_to_offset(cursor, limit, page)

    # Use cached tiered feed for better performance.
    # Videos surface immediately after promotion; AI enrichment backfills later.
    videos, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.VIDEOS,
        limit=limit,
        offset=offset,
        require_ai_processed=False,
        hybrid_video_rerank=flags.is_enabled("video_hybrid_rerank"),
        device_id=x_device_id,
    )

    # Log tier distribution (from cached results)
    tier_counts = {}
    for v in videos:
        tier = v.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Videos cursor {offset}: {tier_counts} (limit={limit})")

    # ALWAYS add diagnostic headers (even on empty)
    if response:
        feed_meta = FeedMetadata(
            generated_at=meta.generated_at,
            source=meta.source,
            cache_key=meta.cache_key,
            cache_hit=meta.cache_hit,
            items=videos,
            surface="videos",
            tier_config=meta.tier_config,
            feed_version=compute_feed_version(videos, meta.generated_at),
        )
        feed_meta.add_headers(response)
    else:
        feed_meta = FeedMetadata(
            generated_at=meta.generated_at,
            source=meta.source,
            cache_key=meta.cache_key,
            cache_hit=meta.cache_hit,
            items=videos,
            surface="videos",
            tier_config=meta.tier_config,
            feed_version=compute_feed_version(videos, meta.generated_at),
        )

    # Ad injection (noop when ADS_ENABLED is false)
    mixed, ads_injected = inject_ads(videos, placement_id="feed_fullpage")
    if response:
        response.headers["X-Ads-Injected"] = str(ads_injected)
        response.headers["X-Ads-Frequency"] = str(settings.ADS_FEED_FREQUENCY)
    inventory_state = _inventory_state(
        item_count=len(mixed),
        remaining_count=getattr(meta, "remaining_window_count", 0),
        offset=offset,
    )
    newest_published_at, newest_created_at = feed_meta.get_newest_dates()
    record_feed_served(
        surface="videos",
        feed_version=feed_meta.feed_version,
        inventory_state=inventory_state,
        items=videos,
    )

    return {
        "items": mixed,
        "next_cursor": str(offset + limit) if has_more else None,
        "has_more": has_more,
        "served_at": meta.generated_at.isoformat(),
        "inventory_state": inventory_state,
        "feed_version": feed_meta.feed_version,
        "newest_published_at": newest_published_at,
        "newest_created_at": newest_created_at,
        "window_days": VIDEOS_WINDOW_DAYS,
        "remaining_count": getattr(meta, "remaining_window_count", 0),
    }


@router.get("/reels", response_model=Dict[str, Any])
def get_reels(
    limit: int = Query(10, ge=1, le=50, description="Number of reels to return"),
    cursor: Optional[str] = Query(None, description="Cursor returned by the previous page"),
    page: Optional[int] = Query(None, ge=1, include_in_schema=False),
    x_device_id: Optional[str] = Header(None, description="Optional device identifier"),
    response: Response = None,
    db: Session = Depends(get_db),
    flags: FeatureFlags = Depends(get_feature_flags),
):
    """
    Get the most recent reels (short videos) using tiered freshness strategy.

    Returns a blend of:
    - Tier A (Fresh): reels published within rolling window (7 days)
    - Tier B (Backfill): reels added recently but published earlier
    - Tier C (Evergreen): older high-quality reels

    REELs don't require AI summaries.
    Results are diversity-mixed to ensure varied source distribution.
    """
    # Check reels feature flag
    if not flags.is_enabled("reels"):
        raise HTTPException(status_code=503, detail="Reels feature is currently disabled")

    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)

    offset = _cursor_to_offset(cursor, limit, page)

    # Use cached tiered feed for better performance
    videos, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.REELS,
        limit=limit,
        offset=offset,
        require_ai_processed=False,  # Reels don't need AI processing
        hybrid_video_rerank=flags.is_enabled("video_hybrid_rerank"),
        device_id=x_device_id,
    )

    # Log tier distribution (from cached results)
    tier_counts = {}
    for v in videos:
        tier = v.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Reels cursor {offset}: {tier_counts} (limit={limit})")

    # Add diagnostic headers
    if response:
        feed_meta = FeedMetadata(
            generated_at=meta.generated_at,
            source=meta.source,
            cache_key=meta.cache_key,
            cache_hit=meta.cache_hit,
            items=videos,
            surface="reels",
            tier_config=meta.tier_config,
            feed_version=compute_feed_version(videos, meta.generated_at),
        )
        feed_meta.add_headers(response)
    else:
        feed_meta = FeedMetadata(
            generated_at=meta.generated_at,
            source=meta.source,
            cache_key=meta.cache_key,
            cache_hit=meta.cache_hit,
            items=videos,
            surface="reels",
            tier_config=meta.tier_config,
            feed_version=compute_feed_version(videos, meta.generated_at),
        )

    inventory_state = _inventory_state(
        item_count=len(videos),
        remaining_count=getattr(meta, "remaining_window_count", 0),
        offset=offset,
    )
    newest_published_at, newest_created_at = feed_meta.get_newest_dates()
    record_feed_served(
        surface="reels",
        feed_version=feed_meta.feed_version,
        inventory_state=inventory_state,
        items=videos,
    )

    return {
        "items": videos,
        "next_cursor": str(offset + limit) if has_more else None,
        "has_more": has_more,
        "served_at": meta.generated_at.isoformat(),
        "inventory_state": inventory_state,
        "feed_version": feed_meta.feed_version,
        "newest_published_at": newest_published_at,
        "newest_created_at": newest_created_at,
        "window_days": REELS_WINDOW_DAYS,
        "remaining_count": getattr(meta, "remaining_window_count", 0),
    }


@router.get("/{video_id}", response_model=VideoSchema)
def get_video(video_id: int, content_repo: ContentItemRepository = Depends(get_content_repo)):
    """Get a specific video by ID."""
    item = content_repo.get_by_id(video_id)
    if not item or item.type not in (ContentType.VIDEO, ContentType.REEL):
        raise not_found_exception("Video", video_id)

    return _content_item_to_video_schema(item)
