"""
Admin endpoint authentication.

Provides a FastAPI dependency that validates the `X-Admin-Key` header
against the configured `ADMIN_API_KEY`.  Fail-closed: if the env var
is empty or unset, ALL admin requests are rejected.
"""

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException

from app.core.config import settings

_ADMIN_UI_SESSION_SALT = b"blips-admin-ui-session:v1"


def is_admin_key_configured() -> bool:
    return bool(settings.ADMIN_API_KEY)


def is_valid_admin_key(provided: str | None) -> bool:
    configured_key = settings.ADMIN_API_KEY
    if not configured_key or not provided:
        return False
    return secrets.compare_digest(provided, configured_key)


def build_admin_ui_session_token() -> str:
    configured_key = settings.ADMIN_API_KEY
    if not configured_key:
        return ""

    digest = hmac.new(
        configured_key.encode("utf-8"),
        _ADMIN_UI_SESSION_SALT,
        hashlib.sha256,
    ).hexdigest()
    return f"v1.{digest}"


def is_valid_admin_ui_session(token: str | None) -> bool:
    expected = build_admin_ui_session_token()
    if not expected or not token:
        return False
    return secrets.compare_digest(token, expected)


def require_admin_key(
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
) -> str:
    """FastAPI dependency – enforce admin API key on protected routes.

    Raises:
        HTTPException 401 – header missing or ADMIN_API_KEY not configured.
        HTTPException 403 – key present but wrong.
    """
    # Fail-closed: reject everything when no key is configured
    if not is_admin_key_configured():
        raise HTTPException(
            status_code=401,
            detail="Admin endpoints are disabled (ADMIN_API_KEY not configured)",
        )

    if not x_admin_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Admin-Key header",
        )

    if not is_valid_admin_key(x_admin_key):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin key",
        )

    return x_admin_key
