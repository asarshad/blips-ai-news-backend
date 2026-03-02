"""URL normalization utilities for ingestion.

Goals:
- Improve idempotency/dedup by stripping tracking parameters and fragments.
- Keep behavior conservative: only remove well-known tracking params.
- Provide stable canonical forms for YouTube watch/shorts URLs.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_reader",
    "utm_referrer",
    "utm_social",
    "utm_social-type",
    "utm_brand",
    "utm_cid",
    "utm_sid",
    "gclid",
    "fbclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


_YT_ID_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([^&\n?#]+)"),
    re.compile(r"youtube\.com/embed/([^&\n?#]+)"),
    re.compile(r"youtube\.com/shorts/([^&\n?#]+)"),
]


def _extract_youtube_id(url: str) -> Optional[str]:
    for pat in _YT_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def normalize_url(url: str, extra_drop_params: Iterable[str] = ()) -> str:
    """Normalize a URL for storage/dedup.

    - Removes fragment
    - Removes known tracking params + any extra_drop_params
    - Normalizes scheme/host casing
    - Strips trailing slash on path (except root)
    - Canonicalizes YouTube URLs to stable watch/shorts form
    """
    if not url:
        return url

    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        video_id = _extract_youtube_id(url)
        if video_id:
            if "/shorts/" in lowered:
                return f"https://www.youtube.com/shorts/{video_id}"
            return f"https://www.youtube.com/watch?v={video_id}"

    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()

    drop = set(_TRACKING_PARAMS)
    drop.update(p.lower() for p in extra_drop_params)

    query_pairs = [(k, v) for (k, v) in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in drop]
    query = urlencode(query_pairs, doseq=True)

    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    # Remove fragments always.
    fragment = ""

    return urlunsplit((scheme, netloc, path, query, fragment))
