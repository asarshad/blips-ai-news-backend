"""Video routes for the REST API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.exceptions import VideoNotFoundError, not_found_exception
from app.schemas.video import Video as VideoSchema, VideoList, VideoCreate
from app.services.video_service import VideoService
from app.services.video_fetcher import VideoFetcher
from app.repositories.video_repo import VideoRepository

router = APIRouter()

# Engagement action weights
ACTION_WEIGHTS = {
    "open": 1,
    "dwell": 2,
    "share": 3
}


def get_video_repo(db: Session = Depends(get_db)) -> VideoRepository:
    """Factory for VideoRepository."""
    return VideoRepository(db)


def get_video_service(video_repo: VideoRepository = Depends(get_video_repo)) -> VideoService:
    """Factory for VideoService with dependencies."""
    return VideoService(video_repo)


@router.get("/recent", response_model=VideoList)
def get_recent_videos(
    limit: int = Query(10, ge=1, le=50, description="Number of videos to return"),
    video_service: VideoService = Depends(get_video_service)
):
    """Get the most recent videos."""
    videos = video_service.get_recent_videos(limit)
    return {"videos": videos}


@router.get("/reels", response_model=VideoList)
def get_reels(
    limit: int = Query(10, ge=1, le=50, description="Number of reels to return"),
    video_service: VideoService = Depends(get_video_service)
):
    """Get the most recent reels (short videos)."""
    videos = video_service.get_reels(limit)
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
    video_service: VideoService = Depends(get_video_service)
):
    """Get a specific video by ID."""
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
