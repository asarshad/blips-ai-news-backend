"""
Device ID helpers.

Provides a deterministic, one-way hash of the client's IP address
and User-Agent so we never store raw PII.
"""

import hashlib
from typing import Optional

from fastapi import Request


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
