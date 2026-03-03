"""Canonical identity helpers for ingestion.

Design goals:
- Deterministic canonical_key generation for hard-dedupe
- No network / side effects
- Conservative URL normalization (reuse normalize_url)

Canonical key rules:
- ARTICLE: sha256(normalized canonical URL or source_url)[:32]
- VIDEO/REEL: YouTube video_id when available, else sha256(normalized source_url)[:32]

The DB enforces uniqueness via a partial unique index on (type, canonical_key)
where canonical_key is not NULL.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from app.ingestion.url_normalizer import normalize_url


def extract_youtube_video_id(url: str) -> Optional[str]:
    if not url:
        return None

    lowered = url.lower()
    if "youtu.be/" in lowered:
        try:
            after = url.split("youtu.be/", 1)[1]
            return after.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0]
        except Exception:
            return None

    if "youtube.com" in lowered:
        if "/shorts/" in lowered:
            try:
                after = url.split("/shorts/", 1)[1]
                return after.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0]
            except Exception:
                return None

        if "watch" in lowered and "v=" in lowered:
            try:
                # normalize_url retains only non-tracking params; v remains.
                normalized = normalize_url(url)
                if "v=" not in normalized:
                    return None
                query = normalized.split("?", 1)[1] if "?" in normalized else ""
                for pair in query.split("&"):
                    if pair.startswith("v="):
                        return pair.split("=", 1)[1]
                return None
            except Exception:
                return None

    return None


def canonical_key_for_article(*, canonical_url: Optional[str], source_url: str) -> Optional[str]:
    base = canonical_url or source_url
    if not base:
        return None
    normalized = normalize_url(base)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def canonical_key_for_youtube(
    *, video_id: Optional[str], source_url: str, video_url: Optional[str]
) -> Optional[str]:
    vid = (
        video_id
        or extract_youtube_video_id(video_url or "")
        or extract_youtube_video_id(source_url or "")
    )
    if vid:
        return vid
    if not source_url:
        return None
    normalized = normalize_url(source_url)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
