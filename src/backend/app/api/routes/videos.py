"""Video routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes tiered freshness strategy (A/B/C) and diversity mixing.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
from app.services.inventory_service import Surface
from app.services.tiered_feed_service import (
    get_cached_tiered_feed,
)
from app.services.topup_service import check_and_trigger_topup

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)



def _content_item_to_video_schema(item) -> dict:
    """Convert ContentItem to Video schema format (backward compatible)."""
    return {
        "id": item.id,
        "title": item.title,
        "summary": item.summary or "",
        "video_url": item.video_url or item.source_url,
        "source_url": item.source_url,
        "thumbnail_url": item.image_url,
        "source": item.source or "YouTube",
        "category": (item.topics[0] if item.topics else "Technology"),
        "duration_seconds": item.duration_seconds,
        "hot_score": int(item.global_score * 100) if item.global_score else 0,
        "created_at": item.created_at.isoformat() if item.created_at else None
    }


@router.get("/recent", response_model=Dict[str, Any])
def get_recent_videos(
    limit: int = Query(10, ge=1, le=50, description="Number of videos to return"),
    page: int = Query(1, ge=1, description="Page number"),
    response: Response = None,
    db: Session = Depends(get_db),
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Get the most recent videos using tiered freshness strategy.
    
    Returns a blend of:
    - Tier A (Fresh): videos published within rolling window
    - Tier B (Backfill): videos added recently but published earlier
    - Tier C (Evergreen): older high-quality videos
    
    Each video includes freshness_tier, published_age_seconds, and added_age_seconds.
    Results are diversity-mixed to ensure varied source distribution.
    """
    # Check videos feature flag
    if not flags.is_enabled("videos"):
        raise HTTPException(
            status_code=503,
            detail="Videos feature is currently disabled"
        )
    
    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)
    
    offset = (page - 1) * limit
    
    # Use cached tiered feed for better performance
    # Only show videos that have been AI-processed (have summaries)
    videos, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.VIDEOS,
        limit=limit,
        offset=offset,
        require_ai_processed=True,
    )
    
    # Log tier distribution (from cached results)
    tier_counts = {}
    for v in videos:
        tier = v.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Videos page {page}: {tier_counts} (limit={limit})")
    
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
    
    if not videos and page == 1:
        raise HTTPException(status_code=404, detail="No videos found")
    
    # Ad injection (noop when ADS_ENABLED is false)
    mixed, ads_injected = inject_ads(videos, placement_id="feed_fullpage")
    if response:
        response.headers["X-Ads-Injected"] = str(ads_injected)
        response.headers["X-Ads-Frequency"] = str(settings.ADS_FEED_FREQUENCY)

    return {
        "videos": mixed,
        "has_more": has_more,
        "page": page,
    }


@router.get("/reels", response_model=Dict[str, Any])
def get_reels(
    limit: int = Query(10, ge=1, le=50, description="Number of reels to return"),
    page: int = Query(1, ge=1, description="Page number"),
    response: Response = None,
    db: Session = Depends(get_db),
    flags: FeatureFlags = Depends(get_feature_flags)
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
        raise HTTPException(
            status_code=503,
            detail="Reels feature is currently disabled"
        )
    
    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)
    
    offset = (page - 1) * limit
    
    # Use cached tiered feed for better performance
    videos, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.REELS,
        limit=limit,
        offset=offset,
        require_ai_processed=False,  # Reels don't need AI processing
    )
    
    # Log tier distribution (from cached results)
    tier_counts = {}
    for v in videos:
        tier = v.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Reels page {page}: {tier_counts} (limit={limit})")
    
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
    
    return {
        "videos": videos,
        "has_more": has_more,
        "page": page,
    }


@router.get("/{video_id}", response_model=VideoSchema)
def get_video(
    video_id: int,
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """Get a specific video by ID."""
    item = content_repo.get_by_id(video_id)
    if not item or item.type not in (ContentType.VIDEO, ContentType.REEL):
        raise not_found_exception("Video", video_id)
    
    return _content_item_to_video_schema(item)
