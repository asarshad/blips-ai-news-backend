"""Anonymous session bootstrap and token lifecycle routes."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.core.session_auth import (
    AuthenticatedSession,
    SessionAuthError,
    SessionAuthResponse,
    SessionCreateRequest,
    SessionRefreshRequest,
    SessionRevokeRequest,
    create_session,
    refresh_session,
    require_session_token,
    revoke_session,
)

router = APIRouter()
_limiter = Limiter(key_func=get_remote_address)


def _require_edge_bootstrap_verification(request: Request) -> None:
    """Require an edge-issued bootstrap verification signal in prod."""
    if settings.ENV != "prod":
        return

    expected = settings.EDGE_SESSION_BOOTSTRAP_SECRET.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Session bootstrap is not configured")

    provided = request.headers.get("X-Edge-Session-Bootstrap", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Session bootstrap not allowed")


@router.post("", response_model=SessionAuthResponse)
@_limiter.limit(settings.RATE_LIMIT_SESSION_CREATE)
def create_anonymous_session(
    request: Request,
    body: SessionCreateRequest,
    db: Session = Depends(get_db),
) -> SessionAuthResponse:
    _require_edge_bootstrap_verification(request)
    try:
        return create_session(
            db,
            platform=body.platform,
            app_version=body.app_version,
        )
    except SessionAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/refresh", response_model=SessionAuthResponse)
@_limiter.limit(settings.RATE_LIMIT_SESSION_REFRESH)
def refresh_anonymous_session(
    request: Request,
    body: SessionRefreshRequest,
    db: Session = Depends(get_db),
) -> SessionAuthResponse:
    del request
    try:
        return refresh_session(db, body.refresh_token)
    except SessionAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/revoke", status_code=204)
def revoke_anonymous_session(
    body: SessionRevokeRequest,
    session: AuthenticatedSession = Depends(require_session_token),
    db: Session = Depends(get_db),
) -> None:
    try:
        revoke_session(
            db,
            device_id=session.device_id,
            refresh_token=body.refresh_token,
        )
    except SessionAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return None
