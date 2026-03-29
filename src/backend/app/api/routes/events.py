"""
Analytics event endpoints — impression and click tracking.

Events are logged for later analysis. No external tracking SDK is
called; the data stays on-server. A future ad provider integration
may forward events to an attribution service.
"""

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.logging import get_logger
from app.core.session_auth import require_session_token
from app.schemas.events import EventPayload, EventResponse
from app.services.freshness_metrics_service import record_client_freshness_event

logger = get_logger(__name__)

router = APIRouter()
_limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/events/impression",
    response_model=EventResponse,
    summary="Record an impression event",
)
@_limiter.limit(settings.RATE_LIMIT_EVENTS)
def record_impression(
    request: Request,
    payload: EventPayload,
    _session=Depends(require_session_token),
) -> EventResponse:
    """Log that a user viewed a feed item or ad."""
    del request
    if payload.event_name:
        record_client_freshness_event(
            event_name=payload.event_name,
            surface=payload.surface,
            count=payload.count or 1,
        )
    logger.info(
        "impression event: type=%s content_id=%s ad_id=%s surface=%s session=%s provider=%s slot=%s unit=%s status=%s event=%s count=%s feed_version=%s tier=%s",
        payload.item_type,
        payload.content_id,
        payload.ad_id,
        payload.surface,
        payload.session_id,
        payload.provider,
        payload.slot_index,
        payload.ad_unit_id,
        payload.load_status,
        payload.event_name,
        payload.count,
        payload.feed_version,
        payload.freshness_tier,
    )
    return EventResponse(status="ok")


@router.post(
    "/events/click",
    response_model=EventResponse,
    summary="Record a click event",
)
@_limiter.limit(settings.RATE_LIMIT_EVENTS)
def record_click(
    request: Request,
    payload: EventPayload,
    _session=Depends(require_session_token),
) -> EventResponse:
    """Log that a user tapped / clicked a feed item or ad."""
    del request
    if payload.event_name:
        record_client_freshness_event(
            event_name=payload.event_name,
            surface=payload.surface,
            count=payload.count or 1,
        )
    logger.info(
        "click event: type=%s content_id=%s ad_id=%s surface=%s session=%s provider=%s slot=%s unit=%s status=%s event=%s count=%s feed_version=%s tier=%s",
        payload.item_type,
        payload.content_id,
        payload.ad_id,
        payload.surface,
        payload.session_id,
        payload.provider,
        payload.slot_index,
        payload.ad_unit_id,
        payload.load_status,
        payload.event_name,
        payload.count,
        payload.feed_version,
        payload.freshness_tier,
    )
    return EventResponse(status="ok")


@router.post(
    "/events/track",
    response_model=EventResponse,
    summary="Record a named analytics event",
)
@_limiter.limit(settings.RATE_LIMIT_EVENTS)
def record_named_event(
    request: Request,
    payload: EventPayload,
    _session=Depends(require_session_token),
) -> EventResponse:
    """Record a generic freshness or product analytics event."""
    del request
    if payload.event_name:
        record_client_freshness_event(
            event_name=payload.event_name,
            surface=payload.surface,
            count=payload.count or 1,
        )
    logger.info(
        "named event: type=%s content_id=%s surface=%s session=%s event=%s count=%s feed_version=%s tier=%s",
        payload.item_type,
        payload.content_id,
        payload.surface,
        payload.session_id,
        payload.event_name,
        payload.count,
        payload.feed_version,
        payload.freshness_tier,
    )
    return EventResponse(status="ok")
