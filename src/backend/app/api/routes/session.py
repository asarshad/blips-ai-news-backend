"""
Session and playlist routes for personalized content delivery.

Endpoints:
- GET /session/playlist: Get personalized playlist (session snapshots)
- POST /session/interactions: Record user interaction
- GET /session/preferences: Get user preferences (debug)
- GET /session/stats: Get user stats (debug)
"""

from enum import Enum
from typing import Dict, List, Optional

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_redis
from app.core.logging import get_logger
from app.models.content import ContentType, EventType, InteractionEvent, UserPreference, UserProfile
from app.models.usage import Usage
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import (
    InteractionEventRepository,
    UserPreferenceRepository,
    UserProfileRepository,
)
from app.services.personalization_service import PersonalizationService
from app.services.playlist_service import PlaylistService

logger = get_logger(__name__)
router = APIRouter()


# ============================================================================
# Request/Response Schemas
# ============================================================================


class ContentTypeParam(str, Enum):
    """Content type parameter for API."""

    ARTICLE = "ARTICLE"
    VIDEO = "VIDEO"
    REEL = "REEL"


class EventTypeParam(str, Enum):
    """Event type parameter for API."""

    VIEW_10S = "VIEW_10S"
    OPEN_SOURCE = "OPEN_SOURCE"
    SHARE = "SHARE"
    SAVE = "SAVE"
    CHAT_START = "CHAT_START"
    CHAT_MESSAGE = "CHAT_MESSAGE"
    VIDEO_IMPRESSION = "VIDEO_IMPRESSION"
    VIDEO_START = "VIDEO_START"
    VIDEO_3S = "VIDEO_3S"
    VIDEO_50PCT = "VIDEO_50PCT"
    VIDEO_95PCT = "VIDEO_95PCT"
    VIDEO_SKIP_LT_2S = "VIDEO_SKIP_LT_2S"
    VIDEO_SAVE = "VIDEO_SAVE"
    VIDEO_SHARE = "VIDEO_SHARE"
    LESS_FROM_CREATOR = "LESS_FROM_CREATOR"
    CAUGHT_UP = "CAUGHT_UP"


class InteractionRequest(BaseModel):
    """Request body for recording an interaction."""

    content_item_id: int
    event_type: EventTypeParam
    extra_data: Optional[dict] = None


class InteractionResponse(BaseModel):
    """Response for interaction recording."""

    success: bool
    event_id: Optional[int] = None
    message: Optional[str] = None


class PlaylistItem(BaseModel):
    """Single item in playlist response."""

    id: int
    type: str
    source: str
    source_url: Optional[str]
    title: str
    description: Optional[str]
    summary: Optional[str]
    image_url: Optional[str]
    video_url: Optional[str]
    duration: Optional[int]
    topics: List[str]
    entities: List[str]
    published_at: Optional[str]
    global_score: Optional[float]
    cluster_id: Optional[str]
    conversation_starters: Optional[Dict[str, List[str]]] = None


class PlaylistResponse(BaseModel):
    """Response for playlist endpoint."""

    items: List[PlaylistItem]
    session_id: str
    cursor: Optional[int]
    has_more: bool
    total_items: int


class PreferencesResponse(BaseModel):
    """Response for preferences endpoint."""

    topics: List[dict]
    entities: List[dict]
    sources: List[dict]
    formats: List[dict]


class UserStatsResponse(BaseModel):
    """Response for user stats endpoint."""

    device_id: str
    user_id: str
    created_at: str
    preference_counts: dict
    engagement_7d: dict


# ============================================================================
# Dependencies
# ============================================================================


def get_playlist_service(
    db: Session = Depends(get_db), redis_client: redis.Redis = Depends(get_redis)
) -> PlaylistService:
    """Factory for PlaylistService with dependencies."""
    from app.services.playlist_service import create_playlist_service

    return create_playlist_service(db, redis_client)


def get_personalization_service(db: Session = Depends(get_db)) -> PersonalizationService:
    """Factory for PersonalizationService with dependencies."""
    content_repo = ContentItemRepository(db)
    profile_repo = UserProfileRepository(db)
    preference_repo = UserPreferenceRepository(db)
    event_repo = InteractionEventRepository(db)

    return PersonalizationService(
        profile_repo=profile_repo,
        preference_repo=preference_repo,
        event_repo=event_repo,
        content_repo=content_repo,
    )


def get_device_id(x_device_id: str = Header(..., description="Device identifier")) -> str:
    """Extract and validate device ID from header."""
    if not x_device_id or len(x_device_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid X-Device-ID header")
    return x_device_id


# ============================================================================
# Playlist Endpoints
# ============================================================================


@router.get("/playlist", response_model=PlaylistResponse)
def get_playlist(
    type: ContentTypeParam = Query(..., description="Content type"),
    size: int = Query(50, ge=10, le=100, description="Items per request"),
    session_id: Optional[str] = Query(None, description="Session ID for continuity"),
    cursor: Optional[int] = Query(None, ge=0, description="Cursor position to continue from"),
    refresh: bool = Query(False, description="Force refresh playlist (new session)"),
    device_id: str = Depends(get_device_id),
    playlist_service: PlaylistService = Depends(get_playlist_service),
):
    """
    Get a personalized playlist for the user session.

    Uses session snapshots: once generated, a playlist is immutable
    for that session. Use cursor-based continuation for swipe feeds.

    Returns ranked content items with diversity constraints:
    - No repeated stories (cluster_id)
    - Topic diversity (max 40% per topic)
    - Source rotation

    Session snapshot cached for 1 hour.
    """
    # Map string enum to model enum
    content_type = ContentType(type.value)

    # Get playlist with session support
    result = playlist_service.get_playlist(
        device_id=device_id,
        content_type=content_type,
        size=size,
        session_id=session_id,
        cursor=cursor,
        force_refresh=refresh,
    )

    return PlaylistResponse(
        items=result["items"],
        session_id=result["session_id"],
        cursor=result["cursor"],
        has_more=result["has_more"],
        total_items=result["total_items"],
    )


# ============================================================================
# Interaction Endpoints
# ============================================================================


@router.post("/interactions", response_model=InteractionResponse)
def record_interaction(
    request: InteractionRequest,
    device_id: str = Depends(get_device_id),
    personalization_service: PersonalizationService = Depends(get_personalization_service),
    playlist_service: PlaylistService = Depends(get_playlist_service),
):
    """
    Record a user interaction with content.

    Interaction types:
    - VIEW_10S: Viewed content for 10+ seconds
    - OPEN_SOURCE: Clicked to open source article/video
    - SHARE: Shared content
    - SAVE: Saved/bookmarked content
    - CHAT_START: Started AI chat about content
    - CHAT_MESSAGE: Sent message in AI chat

    Updates user preferences based on interaction signals.
    """
    # Map string enum to model enum
    event_type = EventType(request.event_type.value)

    # Record interaction
    event = personalization_service.record_interaction(
        device_id=device_id,
        content_item_id=request.content_item_id,
        event_type=event_type,
        extra_data=request.extra_data,
    )

    if not event:
        return InteractionResponse(success=False, message="Failed to record interaction")

    # Invalidate playlist cache on significant interactions
    if event_type in (
        EventType.SAVE,
        EventType.SHARE,
        EventType.VIDEO_SAVE,
        EventType.VIDEO_SHARE,
        EventType.CHAT_START,
        EventType.LESS_FROM_CREATOR,
    ):
        playlist_service.invalidate_user_cache(device_id)

    return InteractionResponse(success=True, event_id=event.id)


# ============================================================================
# Debug/Admin Endpoints
# ============================================================================


@router.get("/preferences", response_model=PreferencesResponse)
def get_preferences(
    device_id: str = Depends(get_device_id),
    personalization_service: PersonalizationService = Depends(get_personalization_service),
):
    """
    Get user's current preferences (debug endpoint).

    Returns learned preferences across topics, entities, sources, and formats.
    """
    prefs = personalization_service.get_user_preferences(device_id)

    return PreferencesResponse(
        topics=[{"key": k, "weight": w} for k, w in prefs.get("TOPIC", [])],
        entities=[{"key": k, "weight": w} for k, w in prefs.get("ENTITY", [])],
        sources=[{"key": k, "weight": w} for k, w in prefs.get("SOURCE", [])],
        formats=[{"key": k, "weight": w} for k, w in prefs.get("FORMAT", [])],
    )


@router.get("/stats", response_model=UserStatsResponse)
def get_user_stats(
    device_id: str = Depends(get_device_id),
    personalization_service: PersonalizationService = Depends(get_personalization_service),
):
    """
    Get user statistics (debug endpoint).

    Returns preference counts and engagement metrics.
    """
    stats = personalization_service.get_user_stats(device_id)

    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])

    return UserStatsResponse(**stats)


@router.get("/playlist-stats")
def get_playlist_stats(playlist_service: PlaylistService = Depends(get_playlist_service)):
    """
    Get playlist generation statistics (admin endpoint).

    Returns candidate counts and diversity metrics per content type.
    """
    return playlist_service.get_playlist_stats()


@router.delete("/cache")
def invalidate_cache(
    device_id: str = Depends(get_device_id),
    playlist_service: PlaylistService = Depends(get_playlist_service),
):
    """
    Invalidate user's playlist cache (debug endpoint).
    """
    playlist_service.invalidate_user_cache(device_id)
    return {"success": True, "message": "Cache invalidated"}


# ============================================================================
# Data Deletion Endpoint
# ============================================================================


class DataDeletionResponse(BaseModel):
    """Response for the data deletion endpoint."""

    success: bool
    deleted: dict
    message: str


@router.delete("/data", response_model=DataDeletionResponse)
def delete_my_data(
    device_id: str = Depends(get_device_id),
    db: Session = Depends(get_db),
):
    """
    Delete all data associated with a device.

    Removes usage records, interaction events, learned preferences, and the
    device profile.  Returns counts of deleted rows per table.
    """
    counts: dict[str, int] = {}

    try:
        counts["usage"] = (
            db.query(Usage).filter(Usage.device_id == device_id).delete(synchronize_session=False)
        )

        counts["interaction_events"] = (
            db.query(InteractionEvent)
            .filter(InteractionEvent.device_id == device_id)
            .delete(synchronize_session=False)
        )

        counts["user_preferences"] = (
            db.query(UserPreference)
            .filter(UserPreference.device_id == device_id)
            .delete(synchronize_session=False)
        )

        counts["user_profiles"] = (
            db.query(UserProfile)
            .filter(UserProfile.device_id == device_id)
            .delete(synchronize_session=False)
        )

        db.commit()
        total = sum(counts.values())
        logger.info(f"[delete_my_data] Deleted {total} rows for device {device_id[:8]}...")

        return DataDeletionResponse(
            success=True,
            deleted=counts,
            message=f"Deleted {total} records across {len([v for v in counts.values() if v > 0])} tables.",
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[delete_my_data] Error for device {device_id[:8]}...: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete data. Please try again."
        ) from e
