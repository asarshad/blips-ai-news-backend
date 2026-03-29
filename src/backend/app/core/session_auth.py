"""Lightweight anonymous bearer-session helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import jwt
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.models.content import UserProfile
from app.models.device_session import DeviceSession

_REFRESH_TOKEN_BYTES = 32
_TOKEN_AUDIENCE = "blips-mobile"
_TOKEN_ISSUER = "blips-api"
_DEV_FALLBACK_ACCESS_SECRET = "dev-session-auth-secret"


class SessionAuthError(Exception):
    """Known session-auth failure with an HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SessionCreateRequest(BaseModel):
    platform: str = Field(..., min_length=2, max_length=32)
    app_version: str = Field(..., min_length=1, max_length=64)


class SessionRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16)


class SessionRevokeRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=16)


class SessionAuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: str


@dataclass(frozen=True)
class AuthenticatedSession:
    device_id: str
    platform: str
    app_version: Optional[str]
    session_expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _access_secret() -> str:
    secret = settings.SESSION_AUTH_ACCESS_SECRET.strip()
    if secret:
        return secret

    if settings.ENV in {"dev", "test"}:
        return _DEV_FALLBACK_ACCESS_SECRET

    raise SessionAuthError(503, "Session auth is not configured")


def _refresh_token_hash(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def _new_device_id() -> str:
    return f"dev_{secrets.token_urlsafe(24)}"


def _issue_access_token(device_id: str) -> tuple[str, datetime]:
    now = _utcnow()
    expires_at = now + timedelta(seconds=settings.SESSION_AUTH_ACCESS_TTL_SECONDS)
    payload = {
        "sub": device_id,
        "aud": _TOKEN_AUDIENCE,
        "iss": _TOKEN_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, _access_secret(), algorithm="HS256"), expires_at


def _decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _access_secret(),
            algorithms=["HS256"],
            audience=_TOKEN_AUDIENCE,
            issuer=_TOKEN_ISSUER,
        )
    except jwt.ExpiredSignatureError as exc:
        raise SessionAuthError(401, "Access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise SessionAuthError(401, "Invalid access token") from exc

    subject = str(payload.get("sub") or "").strip()
    if not subject:
        raise SessionAuthError(401, "Invalid access token subject")
    return payload


def _build_auth_response(device_id: str, refresh_token: str) -> SessionAuthResponse:
    access_token, _ = _issue_access_token(device_id)
    return SessionAuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.SESSION_AUTH_ACCESS_TTL_SECONDS,
        device_id=device_id,
    )


def _apply_rotated_session(
    session: DeviceSession,
    *,
    refresh_token: str,
    platform: Optional[str] = None,
    app_version: Optional[str] = None,
) -> SessionAuthResponse:
    now = _utcnow()
    session.refresh_token_hash = _refresh_token_hash(refresh_token)
    session.issued_at = now
    session.expires_at = now + timedelta(days=settings.SESSION_AUTH_REFRESH_TTL_DAYS)
    session.last_seen_at = now
    session.revoked_at = None
    if platform is not None:
        session.platform = platform
    if app_version is not None:
        session.app_version = app_version
    return _build_auth_response(session.device_id, refresh_token)


def create_session(
    db: Session,
    *,
    platform: str,
    app_version: str,
) -> SessionAuthResponse:
    device_id = _new_device_id()
    now = _utcnow()
    refresh_token = _new_refresh_token()

    db.add(UserProfile(device_id=device_id))
    session = DeviceSession(
        device_id=device_id,
        platform=platform.strip().lower(),
        app_version=app_version.strip(),
        refresh_token_hash=_refresh_token_hash(refresh_token),
        issued_at=now,
        expires_at=now + timedelta(days=settings.SESSION_AUTH_REFRESH_TTL_DAYS),
        last_seen_at=now,
    )
    db.add(session)
    db.commit()

    return _build_auth_response(device_id, refresh_token)


def refresh_session(db: Session, refresh_token: str) -> SessionAuthResponse:
    now = _utcnow()
    session = (
        db.query(DeviceSession)
        .filter(DeviceSession.refresh_token_hash == _refresh_token_hash(refresh_token))
        .one_or_none()
    )
    if session is None:
        raise SessionAuthError(401, "Refresh token is invalid")
    if session.revoked_at is not None:
        raise SessionAuthError(401, "Session has been revoked")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise SessionAuthError(401, "Refresh token has expired")

    response = _apply_rotated_session(session, refresh_token=_new_refresh_token())
    db.commit()
    return response


def revoke_session(
    db: Session,
    *,
    device_id: str,
    refresh_token: Optional[str] = None,
) -> None:
    session = db.query(DeviceSession).filter(DeviceSession.device_id == device_id).one_or_none()
    if session is None:
        raise SessionAuthError(404, "Session not found")

    if refresh_token is not None and not hmac.compare_digest(
        session.refresh_token_hash,
        _refresh_token_hash(refresh_token),
    ):
        raise SessionAuthError(401, "Refresh token is invalid")

    session.revoked_at = _utcnow()
    session.last_seen_at = _utcnow()
    db.commit()


def require_session_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> AuthenticatedSession:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    try:
        payload = _decode_access_token(token.strip())
        device_id = str(payload["sub"])
        session = db.query(DeviceSession).filter(DeviceSession.device_id == device_id).one_or_none()
        if session is None:
            raise SessionAuthError(401, "Session not found")
        if session.revoked_at is not None:
            raise SessionAuthError(401, "Session has been revoked")

        session.last_seen_at = _utcnow()
        db.commit()
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return AuthenticatedSession(
            device_id=device_id,
            platform=session.platform,
            app_version=session.app_version,
            session_expires_at=expires_at,
        )
    except SessionAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
