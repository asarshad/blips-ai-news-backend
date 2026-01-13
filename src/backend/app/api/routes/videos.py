"""Video routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
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
    """
    # Check videos feature flag
    if not flags.is_enabled("videos"):
        raise HTTPException(
            status_code=503,
            detail="Videos feature is currently disabled"
        )
    
    offset = (page - 1) * limit
    
    items = content_repo.get_by_type(
        ContentType.VIDEO,
        limit=limit,
        offset=offset,
        hours_back=720,  # 30 days - ensure enough content available
        ai_processed_only=True
    )
    
    videos = [_content_item_to_video_schema(item) for item in items]
    logger.info(f"Returning {len(videos)} AI-processed videos (page {page})")
    
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
    REELs have a longer time window (30 days) since they're evergreen content.
    """
    # Check reels feature flag
    if not flags.is_enabled("reels"):
        raise HTTPException(
            status_code=503,
            detail="Reels feature is currently disabled"
        )
    
    offset = (page - 1) * limit
    
    items = content_repo.get_by_type(
        ContentType.REEL,
        limit=limit,
        offset=offset,
        hours_back=720,  # 30 days - REELs are more evergreen than articles
        ai_processed_only=False  # REELs don't need AI summaries
    )
    
    videos = [_content_item_to_video_schema(item) for item in items]
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
