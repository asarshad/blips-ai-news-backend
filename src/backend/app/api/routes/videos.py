"""Video routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes diversity mixing to ensure varied source distribution.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.feature_flags import get_feature_flags, FeatureFlags
from app.core.exceptions import not_found_exception
from app.core.logging import get_logger
from app.schemas.video import Video as VideoSchema, VideoList
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentType
from app.services.diversity_mixer import mix_feed

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


def _content_item_to_video_schema(item) -> dict:
    """Convert ContentItem to Video schema format."""
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
        "created_at": item.created_at
    }


@router.get("/recent", response_model=VideoList)
def get_recent_videos(
    limit: int = Query(10, ge=1, le=50, description="Number of videos to return"),
    page: int = Query(1, ge=1, description="Page number"),
    content_repo: ContentItemRepository = Depends(get_content_repo),
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Get the most recent videos.
    Only returns AI-processed videos with valid summaries.
    Results are diversity-mixed to ensure varied source distribution.
    """
    # Check videos feature flag
    if not flags.is_enabled("videos"):
        raise HTTPException(
            status_code=503,
            detail="Videos feature is currently disabled"
        )
    
    offset = (page - 1) * limit
    
    # Fetch more candidates for diversity mixing
    fetch_limit = min(limit * 3, 100)
    
    items = content_repo.get_by_type(
        ContentType.VIDEO,
        limit=fetch_limit,
        offset=offset,
        hours_back=720,  # 30 days - ensure enough content available
        ai_processed_only=True
    )
    
    # Apply diversity mixing
    mixed_items = mix_feed(items, surface="videos", target_size=limit)
    
    videos = [_content_item_to_video_schema(item) for item in mixed_items]
    logger.info(f"Returning {len(videos)} diversity-mixed videos (page {page})")
    
    return {"videos": videos}


@router.get("/reels", response_model=VideoList)
def get_reels(
    limit: int = Query(10, ge=1, le=50, description="Number of reels to return"),
    page: int = Query(1, ge=1, description="Page number"),
    content_repo: ContentItemRepository = Depends(get_content_repo),
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Get the most recent reels (short videos).
    REELs don't require AI summaries but must exist in content_items.
    Results are diversity-mixed to ensure varied source distribution.
    This is especially important for reels which can be dominated by one source.
    """
    # Check reels feature flag
    if not flags.is_enabled("reels"):
        raise HTTPException(
            status_code=503,
            detail="Reels feature is currently disabled"
        )
    
    offset = (page - 1) * limit
    
    # Fetch more candidates for diversity mixing (important for reels)
    fetch_limit = min(limit * 4, 150)  # Larger pool for reels diversity
    
    items = content_repo.get_by_type(
        ContentType.REEL,
        limit=fetch_limit,
        offset=offset,
        hours_back=720,  # 30 days - REELs are more evergreen than articles
        ai_processed_only=False  # REELs don't need AI summaries
    )
    
    # Apply diversity mixing with stricter reels constraints
    mixed_items = mix_feed(items, surface="reels", target_size=limit)
    
    videos = [_content_item_to_video_schema(item) for item in mixed_items]
    logger.info(f"Returning {len(videos)} diversity-mixed reels (page {page})")
    
    return {"videos": videos}


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
