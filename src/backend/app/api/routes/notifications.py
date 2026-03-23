"""Push notification subscription routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.schemas.push import (
    PushSubscriptionDeleteRequest,
    PushSubscriptionResponse,
    PushSubscriptionUpsertRequest,
)
from app.services.push_service import PushNotificationError, PushNotificationService

router = APIRouter()


def get_device_id(x_device_id: str | None = Header(None, alias="X-Device-ID")) -> str:
    """Validate the device identifier used for anonymous push subscriptions."""
    normalized = (x_device_id or "").strip()
    if len(normalized) < 8:
        raise HTTPException(status_code=400, detail="Invalid X-Device-ID header")
    return normalized


def get_push_service(db: Session = Depends(get_db)) -> PushNotificationService:
    """Construct the push notification service."""
    return PushNotificationService(db=db)


@router.put("/notifications/subscription", response_model=PushSubscriptionResponse)
def upsert_push_subscription(
    request: PushSubscriptionUpsertRequest,
    device_id: str = Depends(get_device_id),
    push_service: PushNotificationService = Depends(get_push_service),
) -> PushSubscriptionResponse:
    """Create or refresh a device push subscription."""
    try:
        subscription = push_service.upsert_subscription(
            device_id=device_id,
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
    device_id: str = Depends(get_device_id),
    push_service: PushNotificationService = Depends(get_push_service),
) -> PushSubscriptionResponse:
    """Delete a device push subscription."""
    try:
        push_service.delete_subscription(device_id=device_id, token=request.token)
    except PushNotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PushSubscriptionResponse(
        success=True,
        active=False,
        token=request.token.strip(),
        platform=None,
    )
