"""Push notification subscription routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.schemas.push import (
    PushSubscriptionDeleteRequest,
    PushSubscriptionResponse,
    PushSubscriptionUpsertRequest,
)
from app.services.push_service import PushNotificationError, PushNotificationService

router = APIRouter()


def get_push_service(db: Session = Depends(get_db)) -> PushNotificationService:
    """Construct the push notification service."""
    return PushNotificationService(db=db)


@router.put("/notifications/subscription", response_model=PushSubscriptionResponse)
def upsert_push_subscription(
    request: PushSubscriptionUpsertRequest,
    session: AuthenticatedSession = Depends(require_session_token),
    push_service: PushNotificationService = Depends(get_push_service),
) -> PushSubscriptionResponse:
    """Create or refresh a device push subscription."""
    try:
        subscription = push_service.upsert_subscription(
            device_id=session.device_id,
            token=request.token,
            platform=request.platform,
        )
    except PushNotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PushSubscriptionResponse(
        success=True,
        active=bool(subscription.active),
        token=subscription.token,
        platform=subscription.platform,
    )


@router.delete("/notifications/subscription", response_model=PushSubscriptionResponse)
def delete_push_subscription(
    request: PushSubscriptionDeleteRequest,
    session: AuthenticatedSession = Depends(require_session_token),
    push_service: PushNotificationService = Depends(get_push_service),
) -> PushSubscriptionResponse:
    """Delete a device push subscription."""
    try:
        push_service.delete_subscription(device_id=session.device_id, token=request.token)
    except PushNotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PushSubscriptionResponse(
        success=True,
        active=False,
        token=request.token.strip(),
        platform=None,
    )
