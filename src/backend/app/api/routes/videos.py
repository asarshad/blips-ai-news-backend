"""Video routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.exceptions import VideoNotFoundError, not_found_exception
from app.core.logging import get_logger
from app.schemas.video import Video as VideoSchema, VideoList, VideoCreate
from app.services.video_service import VideoService
from app.services.video_fetcher import VideoFetcher
from app.repositories.video_repo import VideoRepository
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentType

logger = get_logger(__name__)
router = APIRouter()

# Engagement action weights
ACTION_WEIGHTS = {
    "open": 1,
    "dwell": 2,
    "share": 3
}


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


def get_video_repo(db: Session = Depends(get_db)) -> VideoRepository:
    """Factory for VideoRepository."""
    return VideoRepository(db)


def get_video_service(video_repo: VideoRepository = Depends(get_video_repo)) -> VideoService:
    """Factory for VideoService with dependencies."""
    return VideoService(video_repo)


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
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """
    Get the most recent videos.
    Only returns AI-processed videos with valid summaries.
    """
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
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """
    Get the most recent reels (short videos).
    REELs don't require AI summaries but must exist in content_items.
    REELs have a longer time window (30 days) since they're evergreen content.
    """
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


@router.post("/fetch", response_model=dict)
def fetch_videos(video_repo: VideoRepository = Depends(get_video_repo)):
    """Manually trigger fetching of new videos from YouTube channels."""
    video_fetcher = VideoFetcher(video_repo)
    videos = video_fetcher.fetch_latest_videos()
    
    if not videos:
        return {"message": "No new videos found", "count": 0}
    
    saved_count = video_fetcher.save_videos(videos)
    return {"message": f"Successfully fetched and saved {saved_count} new videos", "count": saved_count}


@router.post("/regenerate-summaries", response_model=dict)
def regenerate_video_summaries(
    limit: int = Query(50, ge=1, le=200, description="Number of videos to process"),
    video_repo: VideoRepository = Depends(get_video_repo)
):
    """Regenerate summaries for existing videos using AI."""
    video_fetcher = VideoFetcher(video_repo)
    updated_count = video_fetcher.regenerate_summaries(limit)
    return {"message": f"Successfully regenerated summaries for {updated_count} videos", "count": updated_count}


@router.get("/{video_id}", response_model=VideoSchema)
def get_video(
    video_id: int,
    content_repo: ContentItemRepository = Depends(get_content_repo),
    video_service: VideoService = Depends(get_video_service)
):
    """Get a specific video by ID."""
    # Try content_items first
    item = content_repo.get_by_id(video_id)
    if item and item.type in (ContentType.VIDEO, ContentType.REEL):
        return _content_item_to_video_schema(item)
    
    # Fallback to legacy videos table
    try:
        return video_service.get_video_by_id(video_id)
    except VideoNotFoundError:
        raise not_found_exception("Video", video_id)


@router.post("/", response_model=VideoSchema)
def create_video(
    video: VideoCreate,
    video_service: VideoService = Depends(get_video_service)
):
    """Create a new video entry."""
    if video_service.video_exists(video.video_url):
        raise HTTPException(status_code=400, detail="Video already exists")
    return video_service.create_video(video)


@router.post("/{video_id}/engage")
def engage_video(
    video_id: int,
    action: str = Query(..., regex="^(open|dwell|share)$"),
    video_repo: VideoRepository = Depends(get_video_repo)
):
    """
    Track user engagement with a video.
    
    Actions:
    - open: User opened/viewed the video (weight: 1)
    - dwell: User spent significant time watching (weight: 2)
    - share: User shared the video (weight: 3)
    """
    weight = ACTION_WEIGHTS.get(action, 1)
    video = video_repo.increment_hot_score(video_id, weight)
    
    if not video:
        raise not_found_exception("Video", video_id)
    
    return {"success": True, "video_id": video_id, "action": action, "new_hot_score": video.hot_score}
