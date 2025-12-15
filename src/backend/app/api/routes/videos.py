
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from app.core.dependencies import get_db
from app.models.video import Video
from app.schemas.video import Video as VideoSchema, VideoList, VideoCreate
from app.services.video_service import VideoService
from app.services.video_fetcher import VideoFetcher
from app.repositories.video_repo import VideoRepository

router = APIRouter()


@router.get("/recent", response_model=VideoList)
def get_recent_videos(
    limit: int = Query(10, description="Number of videos to return"),
    db: Session = Depends(get_db)
):
    """
    Get the most recent videos.
    """
    video_repo = VideoRepository(db)
    video_service = VideoService(video_repo)
    videos = video_service.get_recent_videos(limit)
    return {"videos": videos}


@router.post("/fetch", response_model=dict)
def fetch_videos(db: Session = Depends(get_db)):
    """
    Manually trigger fetching of new videos from YouTube channels.
    """
    try:
        video_repo = VideoRepository(db)
        video_fetcher = VideoFetcher(video_repo)
        videos = video_fetcher.fetch_latest_videos()
        
        if not videos:
            return {"message": "No new videos found", "count": 0}
        
        saved_count = video_fetcher.save_videos(videos)
        return {"message": f"Successfully fetched and saved {saved_count} new videos", "count": saved_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching videos: {str(e)}")


@router.get("/{video_id}", response_model=VideoSchema)
def get_video(
    video_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a specific video by ID.
    """
    video_repo = VideoRepository(db)
    video_service = VideoService(video_repo)
    video = video_service.get_video_by_id(video_id)
    
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    return video


@router.post("/", response_model=VideoSchema)
def create_video(
    video: VideoCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new video entry.
    """
    video_repo = VideoRepository(db)
    video_service = VideoService(video_repo)
    
    # Check if video already exists
    if video_service.video_exists(video.video_url):
        raise HTTPException(status_code=400, detail="Video already exists")
    
    return video_service.create_video(video)
