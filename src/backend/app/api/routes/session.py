"""
Session and playlist routes for personalized content delivery.

Endpoints:
- GET /session/playlist: Get personalized playlist (session snapshots)
- POST /session/interactions: Record user interaction
- POST /session/reports: Submit a moderation report
- GET /session/playlist-stats: Get playlist generation stats (internal ops)
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.feed_headers import FeedMetadata
from app.core.auth import require_admin_key
from app.core.dependencies import get_db, get_redis
from app.core.logging import get_logger
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.db.base import SessionLocal
from app.models.content import (
    ContentReport,
    ContentType,
    EventType,
    InteractionEvent,
    UserPreference,
    UserProfile,
)
from app.models.device_session import DeviceSession
from app.models.push import PushSubscription
from app.models.usage import Usage
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import (
    InteractionEventRepository,
    UserPreferenceRepository,
    UserProfileRepository,
)
from app.services.feed_freshness_strategies import feed_freshness_strategies
from app.services.freshness_metrics_service import record_feed_served
from app.services.inventory_service import Surface
from app.services.personalization_service import PersonalizationService
from app.services.playlist_service import PlaylistService
from app.services.tiered_feed_service import invalidate_tiered_feed_cache
from app.services.topup_service import check_and_trigger_topup
from app.video_surface_rules import effective_content_type

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


class ReportRequest(BaseModel):
    """Request body for submitting a content report."""

    content_item_id: int
    # Surface: 'articles', 'videos', 'reels', 'chat'
    surface: str
    # Reason: 'hateful', 'violence', 'explicit', 'spam', 'misinformation',
    #         'other', 'blocked_source'
    reason: str
    # For chat reports — identifies the specific AI message
    message_id: Optional[str] = None


class ReportResponse(BaseModel):
    """Response for content report submission."""

    success: bool
    report_id: Optional[int] = None
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
    created_at: Optional[str] = None
    freshness_tier: Optional[str] = None
    freshness_reason: Optional[str] = None
    published_age_seconds: Optional[int] = None
    added_age_seconds: Optional[int] = None
    read_time_minutes: Optional[int] = None
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    category: Optional[str] = None
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
    inventory_state: Optional[str] = None
    served_at: Optional[str] = None
    feed_version: Optional[str] = None
    newest_published_at: Optional[str] = None
    newest_created_at: Optional[str] = None
    remaining_count: int = 0
    freshness_strategy: Optional[str] = None
    freshness_strategy_source: Optional[str] = None
    resume_continuity_window_minutes: Optional[int] = None
    resume_snapshot_after_remote_window: bool = True


class PlaylistMetadataResponse(BaseModel):
    """Lightweight feed-head metadata for freshness checks."""

    served_at: Optional[str] = None
    feed_version: Optional[str] = None
    newest_published_at: Optional[str] = None
    newest_created_at: Optional[str] = None
    freshness_strategy: Optional[str] = None
    freshness_strategy_source: Optional[str] = None


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


def _tiered_surfaces_for_interaction(content_item, event_type: EventType) -> List[Surface]:
    """Map an interaction to the device-scoped tiered feed caches it affects."""
    if event_type == EventType.LESS_FROM_CREATOR:
        return [Surface.VIDEOS, Surface.REELS]

    effective_type = effective_content_type(content_item)
    if event_type == EventType.VIDEO_SKIP_LT_2S:
        return [Surface.REELS] if effective_type == ContentType.REEL else [Surface.VIDEOS]

    if effective_type == ContentType.ARTICLE:
        surfaces = [Surface.ARTICLES]
    elif effective_type == ContentType.REEL:
        surfaces = [Surface.REELS]
    else:
        surfaces = [Surface.VIDEOS]

    affected: List[Surface] = []
    for surface in surfaces:
        signals = feed_freshness_strategies.resolve(surface).feedback_signals(surface=surface)
        if event_type in signals.consumed_event_types or event_type in signals.exposed_event_types:
            affected.append(surface)
    return affected


# ============================================================================
# Playlist Endpoints
# ============================================================================


@router.get("/playlist", response_model=PlaylistResponse)
def get_playlist(
    response: Response,
    type: ContentTypeParam = Query(..., description="Content type"),
    size: int = Query(50, ge=10, le=100, description="Items per request"),
    session_id: Optional[str] = Query(None, description="Session ID for continuity"),
    cursor: Optional[int] = Query(None, ge=0, description="Cursor position to continue from"),
    refresh: bool = Query(False, description="Force refresh playlist (new session)"),
    session: AuthenticatedSession = Depends(require_session_token),
    db: Session = Depends(get_db),
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

    # Trigger background top-up when inventory is thin.
    check_and_trigger_topup(db, SessionLocal)

    # Get playlist with session support
    result = playlist_service.get_playlist(
        device_id=session.device_id,
        content_type=content_type,
        size=size,
        session_id=session_id,
        cursor=cursor,
        force_refresh=refresh,
    )

    generated_at = (
        datetime.fromisoformat(result["served_at"])
        if result.get("served_at")
        else datetime.utcnow()
    )

    feed_meta = FeedMetadata(
        generated_at=generated_at,
        source=result.get("source", "db"),
        cache_key=result.get("cache_key"),
        cache_hit=bool(result.get("cache_hit", False)),
        items=result["items"],
        surface={
            ContentType.ARTICLE: "articles",
            ContentType.VIDEO: "videos",
            ContentType.REEL: "reels",
        }[content_type],
        feed_version=result.get("feed_version"),
        strategy_name=result.get("freshness_strategy"),
        strategy_source=result.get("freshness_strategy_source"),
        resume_continuity_window_minutes=result.get(
            "resume_continuity_window_minutes"
        ),
        resume_snapshot_after_remote_window=result.get(
            "resume_snapshot_after_remote_window"
        ),
    )
    newest_published_at, newest_created_at = feed_meta.get_newest_dates()
    feed_meta.add_headers(response)
    record_feed_served(
        surface={
            ContentType.ARTICLE: "articles",
            ContentType.VIDEO: "videos",
            ContentType.REEL: "reels",
        }[content_type],
        feed_version=result.get("feed_version"),
        inventory_state=result.get("inventory_state"),
        items=result["items"],
    )

    return PlaylistResponse(
        items=result["items"],
        session_id=result["session_id"],
        cursor=result["cursor"],
        has_more=result["has_more"],
        total_items=result["total_items"],
        inventory_state=result.get("inventory_state"),
        served_at=result.get("served_at"),
        feed_version=result.get("feed_version"),
        newest_published_at=newest_published_at,
        newest_created_at=newest_created_at,
        remaining_count=int(result.get("remaining_count", 0) or 0),
        freshness_strategy=result.get("freshness_strategy"),
        freshness_strategy_source=result.get("freshness_strategy_source"),
        resume_continuity_window_minutes=result.get("resume_continuity_window_minutes"),
        resume_snapshot_after_remote_window=bool(
            result.get("resume_snapshot_after_remote_window", True)
        ),
    )


@router.get("/playlist/meta", response_model=PlaylistMetadataResponse)
def get_playlist_metadata(
    response: Response,
    type: ContentTypeParam = Query(..., description="Content type"),
    session: AuthenticatedSession = Depends(require_session_token),
    playlist_service: PlaylistService = Depends(get_playlist_service),
):
    """Return lightweight head metadata without creating or advancing a session."""
    content_type = ContentType[type.value]
    result = playlist_service.get_playlist_metadata(
        device_id=session.device_id,
        content_type=content_type,
    )

    generated_at = (
        datetime.fromisoformat(result["served_at"])
        if result.get("served_at")
        else datetime.utcnow()
    )
    feed_meta = FeedMetadata(
        generated_at=generated_at,
        source=result.get("source", "db"),
        cache_key=result.get("cache_key"),
        cache_hit=bool(result.get("cache_hit", False)),
        items=[],
        surface={
            ContentType.ARTICLE: "articles",
            ContentType.VIDEO: "videos",
            ContentType.REEL: "reels",
        }[content_type],
        feed_version=result.get("feed_version"),
        strategy_name=result.get("freshness_strategy"),
        strategy_source=result.get("freshness_strategy_source"),
    )
    feed_meta.add_headers(response)
    if result.get("newest_published_at"):
        response.headers["X-Newest-Published-At"] = result["newest_published_at"]
    if result.get("newest_created_at"):
        response.headers["X-Newest-Created-At"] = result["newest_created_at"]

    return PlaylistMetadataResponse(
        served_at=result.get("served_at"),
        feed_version=result.get("feed_version"),
        newest_published_at=result.get("newest_published_at"),
        newest_created_at=result.get("newest_created_at"),
        freshness_strategy=result.get("freshness_strategy"),
        freshness_strategy_source=result.get("freshness_strategy_source"),
    )


# ============================================================================
# Interaction Endpoints
# ============================================================================


@router.post("/interactions", response_model=InteractionResponse)
def record_interaction(
    request: InteractionRequest,
    session: AuthenticatedSession = Depends(require_session_token),
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
        device_id=session.device_id,
        content_item_id=request.content_item_id,
        event_type=event_type,
        extra_data=request.extra_data,
    )

    if not event:
        return InteractionResponse(success=False, message="Failed to record interaction")

    content_item = None
    if event_type in (
        EventType.VIEW_10S,
        EventType.VIDEO_IMPRESSION,
        EventType.SAVE,
        EventType.SHARE,
        EventType.OPEN_SOURCE,
        EventType.VIDEO_SAVE,
        EventType.VIDEO_SHARE,
        EventType.VIDEO_50PCT,
        EventType.VIDEO_95PCT,
        EventType.VIDEO_SKIP_LT_2S,
        EventType.CHAT_START,
        EventType.CHAT_MESSAGE,
        EventType.LESS_FROM_CREATOR,
    ):
        content_item = personalization_service.content_repo.get_by_id(request.content_item_id)
        if content_item:
            affected_surfaces = _tiered_surfaces_for_interaction(content_item, event_type)
            if affected_surfaces:
                playlist_service.invalidate_user_cache(session.device_id)
            for surface in affected_surfaces:
                invalidate_tiered_feed_cache(surface=surface, device_id=session.device_id)

    return InteractionResponse(success=True, event_id=event.id)


@router.post("/reports", response_model=ReportResponse)
def submit_report(
    request: ReportRequest,
    session: AuthenticatedSession = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    Submit a moderation report for a content item or AI chat message.

    Reason values:
    - hateful: Hateful or discriminatory content
    - violence: Violent or graphic content
    - explicit: Sexually explicit content
    - spam: Spam or misleading content
    - misinformation: False or misleading information
    - other: Other objectionable content
    - blocked_source: User blocked this source (also triggers LESS_FROM_CREATOR)
    """
    try:
        report = ContentReport(
            device_id=session.device_id,
            content_item_id=request.content_item_id,
            surface=request.surface,
            reason=request.reason,
            message_id=request.message_id,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        logger.info(
            "Content report submitted",
            extra={
                "device_id": session.device_id,
                "content_item_id": request.content_item_id,
                "surface": request.surface,
                "reason": request.reason,
                "report_id": report.id,
            },
        )
        return ReportResponse(success=True, report_id=report.id)
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to save content report",
            extra={
                "device_id": session.device_id,
                "content_item_id": request.content_item_id,
                "error": str(exc),
            },
        )
        return ReportResponse(success=False, message="Failed to submit report")


# ============================================================================
# Internal diagnostics endpoint
# ============================================================================


@router.get("/playlist-stats")
def get_playlist_stats(
    _admin_key: str = Depends(require_admin_key),
    playlist_service: PlaylistService = Depends(get_playlist_service),
):
    """
    Get playlist generation statistics (admin endpoint).

    Returns candidate counts and diversity metrics per content type.
    """
    return playlist_service.get_playlist_stats()


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
    session: AuthenticatedSession = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    Delete all data associated with a device.

    Removes usage records, interaction events, learned preferences, and the
    device profile.  Returns counts of deleted rows per table.
    """
    counts: dict[str, int] = {}

    try:
        counts["push_subscriptions"] = (
            db.query(PushSubscription).filter(PushSubscription.device_id == session.device_id).count()
        )

        counts["usage"] = (
            db.query(Usage)
            .filter(Usage.device_id == session.device_id)
            .delete(synchronize_session=False)
        )

        counts["interaction_events"] = (
            db.query(InteractionEvent)
            .filter(InteractionEvent.device_id == session.device_id)
            .delete(synchronize_session=False)
        )

        counts["user_preferences"] = (
            db.query(UserPreference)
            .filter(UserPreference.device_id == session.device_id)
            .delete(synchronize_session=False)
        )

        counts["device_sessions"] = (
            db.query(DeviceSession)
            .filter(DeviceSession.device_id == session.device_id)
            .delete(synchronize_session=False)
        )

        counts["user_profiles"] = (
            db.query(UserProfile)
            .filter(UserProfile.device_id == session.device_id)
            .delete(synchronize_session=False)
        )

        db.commit()
        total = sum(counts.values())
        logger.info(f"[delete_my_data] Deleted {total} rows for device {session.device_id[:8]}...")

        return DataDeletionResponse(
            success=True,
            deleted=counts,
            message=f"Deleted {total} records across {len([v for v in counts.values() if v > 0])} tables.",
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[delete_my_data] Error for device {session.device_id[:8]}...: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete data. Please try again."
        ) from e
