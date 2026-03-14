"""
Analytics event endpoints — impression and click tracking.

Events are logged for later analysis.  No external tracking SDK is
called; the data stays on-server.  A future ad provider integration
may forward events to an attribution service.
"""

from fastapi import APIRouter

from app.core.logging import get_logger
from app.schemas.events import EventPayload, EventResponse

logger = get_logger(__name__)

router = APIRouter()


@router.post(
    "/events/impression",
    response_model=EventResponse,
    summary="Record an impression event",
)
def record_impression(payload: EventPayload) -> EventResponse:
    """Log that a user viewed a feed item or ad."""
    logger.info(
        "impression event: type=%s content_id=%s ad_id=%s surface=%s session=%s provider=%s slot=%s unit=%s status=%s",
        payload.item_type,
        payload.content_id,
        payload.ad_id,
        payload.surface,
        payload.session_id,
        payload.provider,
        payload.slot_index,
        payload.ad_unit_id,
        payload.load_status,
    )
    return EventResponse(status="ok")


@router.post(
    "/events/click",
    response_model=EventResponse,
    summary="Record a click event",
)
def record_click(payload: EventPayload) -> EventResponse:
    """Log that a user tapped / clicked a feed item or ad."""
    logger.info(
        "click event: type=%s content_id=%s ad_id=%s surface=%s session=%s provider=%s slot=%s unit=%s status=%s",
        payload.item_type,
        payload.content_id,
        payload.ad_id,
        payload.surface,
        payload.session_id,
        payload.provider,
        payload.slot_index,
        payload.ad_unit_id,
        payload.load_status,
    )
    return EventResponse(status="ok")
