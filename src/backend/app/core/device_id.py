"""
Device ID helpers.

Provides:
- deterministic hashed fallback IDs derived from IP + User-Agent
- validation for explicit client-supplied device identifiers
- request helpers that prefer explicit device identifiers on app-facing routes
"""

import hashlib
import re
from typing import Optional

from fastapi import HTTPException, Request

_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def hash_device_id(ip: str, user_agent: Optional[str] = None) -> str:
    """Return a 32-char hex SHA-256 hash of *ip* + *user_agent*.

    Deterministic: the same (ip, user_agent) pair always produces the
    same hash.  One-way: the raw IP cannot be recovered.
    """
    raw = ip
    if user_agent:
        raw = f"{ip}_{user_agent[:50]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_device_id(request: Request, user_agent: Optional[str] = None) -> str:
    """Build a hashed device identifier from the current request."""
    client_ip = request.client.host if request.client else "unknown"
    ua = user_agent or request.headers.get("user-agent", "")
    return hash_device_id(client_ip, ua)


def validate_device_id(device_id: str) -> str:
    """Validate a client-supplied device identifier."""
    candidate = (device_id or "").strip()
    if not _DEVICE_ID_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Invalid X-Device-ID header")
    return candidate


def resolve_device_id(
    request: Request,
    *,
    x_device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    allow_fallback: bool = True,
) -> str:
    """Resolve the effective device identifier for a request.

    Prefer an explicit ``X-Device-ID`` header when present. Fall back to the
    hashed IP/User-Agent identifier only when the caller has not provided a
    header and the route explicitly allows fallback behavior.
    """
    if x_device_id is not None:
        return validate_device_id(x_device_id)

    if not allow_fallback:
        raise HTTPException(status_code=400, detail="Missing X-Device-ID header")

    return get_device_id(request, user_agent)
