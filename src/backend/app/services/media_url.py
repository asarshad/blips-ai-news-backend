"""Helpers for public media URLs returned by feed APIs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from app.core.config import settings

_DEFAULT_PUBLIC_API_ORIGIN = "https://api.blips.tech"


def public_media_url(value: Any) -> str | None:
    """Return an absolute media URL when a stored URL is API-relative."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if not text.startswith("/"):
        return text

    base = (settings.API_PUBLIC_BASE_URL or _DEFAULT_PUBLIC_API_ORIGIN).strip()
    if not base.endswith("/"):
        base += "/"
    return urljoin(base, text.lstrip("/"))
