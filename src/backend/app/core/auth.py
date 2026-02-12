"""
Admin endpoint authentication.

Provides a FastAPI dependency that validates the `X-Admin-Key` header
against the configured `ADMIN_API_KEY`.  Fail-closed: if the env var
is empty or unset, ALL admin requests are rejected.
"""

import secrets

from fastapi import Header, HTTPException

from app.core.config import settings


def require_admin_key(
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
) -> str:
    """FastAPI dependency – enforce admin API key on protected routes.

    Raises:
        HTTPException 401 – header missing or ADMIN_API_KEY not configured.
        HTTPException 403 – key present but wrong.
    """
    configured_key = settings.ADMIN_API_KEY

    # Fail-closed: reject everything when no key is configured
    if not configured_key:
        raise HTTPException(
            status_code=401,
            detail="Admin endpoints are disabled (ADMIN_API_KEY not configured)",
        )

    if not x_admin_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Admin-Key header",
        )

    if not secrets.compare_digest(x_admin_key, configured_key):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin key",
        )

    return x_admin_key
