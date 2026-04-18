"""Source-branded SVG placeholder for articles with no recoverable image.

When the image verification pipeline exhausts every recovery attempt
(og:image, extraction, LLM fallback), an article is otherwise stuck in
``missing_article_image`` and never reaches the feed. This module renders
a deterministic, source-branded SVG (served by the public placeholder
route) so the article can become READY with a meaningful hero image
instead of sitting in a pending-forever state.

The placeholder design is intentionally simple and computed purely from
the source + optional category — no external assets, no DB writes, no
third-party services. That keeps it safe to invoke from worker jobs,
cheap to serve at the edge, and easy to render on any client that can
load an SVG URL.
"""

from __future__ import annotations

import hashlib
import html
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlencode, urljoin

from app.core.config import get_settings

_SETTINGS = get_settings()


# NOTE: we intentionally avoid a ``.svg`` suffix so the placeholder URL
# passes the shared image-URL validator (SVG extensions are rejected as
# unsupported). The response still carries the ``image/svg+xml`` MIME type.
PLACEHOLDER_URL_MARKER = "/placeholder/source"


_CATEGORY_PALETTE: dict[str, tuple[str, str]] = {
    "ai": ("#4F46E5", "#7C3AED"),
    "artificial_intelligence": ("#4F46E5", "#7C3AED"),
    "technology": ("#1D4ED8", "#0369A1"),
    "science": ("#0F766E", "#0369A1"),
    "business": ("#15803D", "#0F766E"),
    "finance": ("#15803D", "#166534"),
    "health": ("#BE185D", "#9D174D"),
    "politics": ("#475569", "#1E293B"),
    "sports": ("#EA580C", "#B45309"),
    "entertainment": ("#D97706", "#B45309"),
}

_FALLBACK_PALETTE: tuple[tuple[str, str], ...] = (
    ("#0F172A", "#1E3A8A"),
    ("#1E1B4B", "#4338CA"),
    ("#052E16", "#14532D"),
    ("#3B0764", "#6D28D9"),
    ("#0C4A6E", "#0369A1"),
    ("#7C2D12", "#B45309"),
    ("#831843", "#BE185D"),
    ("#164E63", "#0E7490"),
)


def _normalize_source(source: Optional[str]) -> str:
    value = (source or "").strip()
    return value or "Blips"


def _normalize_category(category: Optional[str]) -> Optional[str]:
    value = (category or "").strip().lower()
    if not value:
        return None
    return value.replace(" ", "_")


def _palette_for(source: str, category: Optional[str]) -> tuple[str, str]:
    if category and category in _CATEGORY_PALETTE:
        return _CATEGORY_PALETTE[category]
    digest = hashlib.sha1(source.lower().encode("utf-8")).digest()
    index = digest[0] % len(_FALLBACK_PALETTE)
    return _FALLBACK_PALETTE[index]


def _initials_for(source: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in source)
    words = [word for word in cleaned.split() if word]
    if not words:
        return source.strip()[:2].upper() or "BL"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _estimate_wordmark_font_size(text: str, *, width: int) -> int:
    # Rough upper bound: SVG averages ~0.55em per character for a bold sans-serif.
    # Target the wordmark filling ~68% of the inner width.
    if not text:
        return 96
    target_width = width * 0.68
    per_char = max(1, len(text)) * 0.55
    size = int(target_width / per_char)
    return max(56, min(size, 148))


def render_source_placeholder_svg(
    *,
    source: Optional[str],
    category: Optional[str] = None,
    width: int = 1200,
    height: int = 630,
) -> str:
    """Return a deterministic source-branded SVG string."""
    normalized_source = _normalize_source(source)
    normalized_category = _normalize_category(category)
    start_color, end_color = _palette_for(normalized_source, normalized_category)

    wordmark = normalized_source.upper()
    initials = _initials_for(normalized_source)
    badge_text = (normalized_category or "news").replace("_", " ").upper()

    wordmark_size = _estimate_wordmark_font_size(wordmark, width=width)

    # Grid lines + watermark initials create a subtle "logo" feel without
    # requiring per-source assets.
    watermark_opacity = 0.08
    wordmark_escaped = html.escape(wordmark)
    initials_escaped = html.escape(initials)
    badge_escaped = html.escape(badge_text)

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{wordmark_escaped}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{start_color}"/>
      <stop offset="100%" stop-color="{end_color}"/>
    </linearGradient>
    <radialGradient id="spot" cx="15%" cy="15%" r="70%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
      <path d="M 48 0 L 0 0 0 48" fill="none" stroke="#ffffff" stroke-opacity="0.05" stroke-width="1"/>
    </pattern>
    <filter id="soft" x="-10%" y="-10%" width="120%" height="120%">
      <feGaussianBlur stdDeviation="0.6"/>
    </filter>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect width="100%" height="100%" fill="url(#grid)"/>
  <rect width="100%" height="100%" fill="url(#spot)"/>
  <g font-family="'Inter','Helvetica Neue',Arial,sans-serif" fill="#ffffff">
    <text x="{width // 2}" y="{height // 2 + 40}" text-anchor="middle" font-size="{width // 2}" font-weight="900" fill="#ffffff" fill-opacity="{watermark_opacity}" letter-spacing="-6">{initials_escaped}</text>
    <text x="{width // 2}" y="{height // 2 + wordmark_size // 3}" text-anchor="middle" font-size="{wordmark_size}" font-weight="800" letter-spacing="4" filter="url(#soft)">{wordmark_escaped}</text>
    <g transform="translate({width // 2}, {height // 2 + wordmark_size // 3 + 70})" text-anchor="middle">
      <rect x="-120" y="-28" width="240" height="48" rx="24" fill="#000000" fill-opacity="0.35"/>
      <text y="4" font-size="22" font-weight="700" letter-spacing="6">{badge_escaped}</text>
    </g>
  </g>
</svg>
""".strip()
    return svg


def build_source_placeholder_url(
    *,
    source: Optional[str],
    category: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Build an absolute (or relative) URL for the source-branded placeholder."""
    normalized_source = _normalize_source(source)
    params: dict[str, str] = {"source": normalized_source}
    normalized_category = _normalize_category(category)
    if normalized_category:
        params["category"] = normalized_category

    query = urlencode(params, quote_via=quote)
    relative_path = f"/api/v1{PLACEHOLDER_URL_MARKER}?{query}"

    resolved_base = (base_url if base_url is not None else _SETTINGS.API_PUBLIC_BASE_URL).strip()
    if not resolved_base:
        return relative_path
    if not resolved_base.endswith("/"):
        resolved_base = resolved_base + "/"
    return urljoin(resolved_base, relative_path.lstrip("/"))


def is_placeholder_image_url(candidate: Optional[str]) -> bool:
    """Return True when the candidate URL was produced by this module."""
    if not candidate:
        return False
    return PLACEHOLDER_URL_MARKER in candidate


def should_apply_placeholder(
    item: Any,
    *,
    now: Optional[datetime] = None,
    min_age_minutes: Optional[int] = None,
) -> bool:
    """Decide whether an article is due for the source-branded placeholder.

    The caller is expected to have already exhausted the real-image recovery
    paths. We still require that the article has been examined at least once
    (``article_image_checked_at`` populated) and that enough time has passed
    since that first check before committing to the placeholder — giving the
    regular retry loop a chance to find a real image first.
    """
    if not _SETTINGS.ARTICLE_IMAGE_PLACEHOLDER_ENABLED:
        return False

    existing_image_url = (getattr(item, "image_url", None) or "").strip()
    if existing_image_url and not is_placeholder_image_url(existing_image_url):
        return False

    checked_at = getattr(item, "article_image_checked_at", None)
    if checked_at is None:
        return False

    threshold_minutes = (
        min_age_minutes
        if min_age_minutes is not None
        else _SETTINGS.ARTICLE_IMAGE_PLACEHOLDER_MIN_AGE_MINUTES
    )
    current = now or datetime.utcnow()
    elapsed_seconds = (current - checked_at).total_seconds()
    return elapsed_seconds >= max(0, int(threshold_minutes) * 60)


def apply_source_placeholder(
    item: Any,
    *,
    base_url: Optional[str] = None,
) -> bool:
    """Assign a source-branded placeholder URL to ``item.image_url``.

    Returns True when a placeholder URL was written, False otherwise (for
    example when the feature flag is disabled). Readiness/verification
    finalization must be invoked by the caller.
    """
    if not _SETTINGS.ARTICLE_IMAGE_PLACEHOLDER_ENABLED:
        return False

    placeholder_url = build_source_placeholder_url(
        source=getattr(item, "source", None),
        category=_primary_category_for(item),
        base_url=base_url,
    )
    if not placeholder_url:
        return False

    item.image_url = placeholder_url
    return True


def _primary_category_for(item: Any) -> Optional[str]:
    topics = getattr(item, "topics", None) or []
    if isinstance(topics, (list, tuple)) and topics:
        first = topics[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None
