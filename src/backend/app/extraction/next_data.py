"""Next.js __NEXT_DATA__ extraction for SPA-heavy news sites.

Many news sites (CNET, Engadget, The Verge, etc.) are Next.js SPAs.
The httpx fetcher receives a pre-hydration HTML shell whose <article> body
is empty — article content only exists after the browser runs the JS bundle.

However, Next.js embeds a ``<script id="__NEXT_DATA__" type="application/json">``
tag in that same server-rendered shell, containing the full page props
(including the article body) as serialised JSON.

This module:
  1. Finds and parses the __NEXT_DATA__ JSON from raw HTML.
  2. Walks per-domain candidate paths to locate the article body field.
  3. Returns plain text (stripping HTML markup if the body is rich-text HTML).

If extraction fails or the domain/path is not recognised, returns ``None``
so the caller falls through to trafilatura / readability.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── __NEXT_DATA__ tag extractor ───────────────────────────────────────────────

# Matches: <script id="__NEXT_DATA__" type="application/json">{...}</script>
# Uses \b and non-greedy quantifiers to minimise backtracking on malformed HTML.
_NEXT_DATA_RE = re.compile(
    r"""<script\b[^>]*?\bid=(?:"|')__NEXT_DATA__(?:"|')[^>]*>(.*?)</script>""",
    re.DOTALL,
)

# Pre-compiled pattern to detect actual HTML markup in a string.
# Matches opening/closing tags like <p>, </p>, <br/>, etc.
# Used to decide whether to pass a field value through BeautifulSoup.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,200}>")


def extract_next_data(html: str) -> Optional[dict]:
    """Parse the ``__NEXT_DATA__`` JSON blob embedded in a Next.js page.

    Returns the parsed dict, or ``None`` if the tag is absent or malformed.
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("[next_data] JSON parse error: %s", exc)
        return None


# ── Path walker ───────────────────────────────────────────────────────────────

# Matches array-index notation within a single key segment, e.g. "responses[0]"
_INDEX_RE = re.compile(r"^(\w+)\[(\d+)\]$")


def _walk(data: Any, keys: list[str]) -> Any:
    """Walk a list of key segments into a nested dict/list structure.

    Supports both plain keys (``"pageProps"``) and indexed keys
    (``"responses[0]"``).  Returns ``None`` on any missing step.
    """
    for key in keys:
        if data is None:
            return None
        m = _INDEX_RE.match(key)
        if m:
            field, idx = m.group(1), int(m.group(2))
            if not isinstance(data, dict) or field not in data:
                return None
            container = data[field]
            if not isinstance(container, list) or idx >= len(container):
                return None
            data = container[idx]
        else:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
    return data


# ── Per-domain path candidates ────────────────────────────────────────────────

# Each domain maps to a list of dot-notation paths tried in order.
# The first path that resolves to a non-empty string wins.
# Array indices are expressed as  fieldName[n]  within a segment.

_DOMAIN_PATHS: dict[str, list[str]] = {
    "cnet.com": [
        "props.pageProps.article.body",
        "props.pageProps.article.content",
        "props.pageProps.data.article.body",
        "props.pageProps.data.body",
        "props.pageProps.data.content",
    ],
    "engadget.com": [
        "props.pageProps.article.body",
        "props.pageProps.article.content",
        "props.pageProps.post.body",
        "props.pageProps.data.article.body",
        "props.pageProps.data.content",
    ],
    "9to5mac.com": [
        "props.pageProps.article.body",
        "props.pageProps.post.content",
        "props.pageProps.data.content",
    ],
    "theverge.com": [
        "props.pageProps.hydration.responses[0].data.article.body",
        "props.pageProps.hydration.responses[0].data.story.body",
        "props.pageProps.article.body",
        "props.pageProps.story.body",
    ],
    "arstechnica.com": [
        "props.pageProps.article.body",
        "props.pageProps.article.content",
        "props.pageProps.post.content",
    ],
}

# Generic paths tried for any domain not explicitly listed above.
_GENERIC_PATHS: list[str] = [
    "props.pageProps.article.body",
    "props.pageProps.article.content",
    "props.pageProps.post.body",
    "props.pageProps.post.content",
    "props.pageProps.data.body",
    "props.pageProps.data.content",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _domain_root(url: str) -> str:
    """Return the registrable domain (e.g. ``"cnet.com"``) for a URL.

    Strips subdomains (``www.``, ``m.``, etc.) by keeping only the last
    two dot-separated labels.  IP addresses are returned as-is.
    """
    host = urlparse(url).hostname or ""
    if not host:
        return ""
    # IP addresses (v4 starts with digit, v6 starts with "[") — no label stripping.
    if host[0].isdigit() or host[0] == "[":
        return host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _resolve_path(data: dict, path: str) -> Optional[str]:
    """Walk *path* (dot-separated) into *data* and return a string value."""
    value = _walk(data, path.split("."))
    if isinstance(value, str):
        return value.strip() or None
    return None


def _html_to_text(body: str) -> str:
    """Strip HTML markup from an article body field and return plain text.

    Falls back to the raw string if BeautifulSoup fails (e.g. parser error).
    Uses html.parser (stdlib) — faster than lxml for simple get_text() calls.
    """
    try:
        return BeautifulSoup(body, "html.parser").get_text(separator="\n", strip=True)
    except Exception as exc:
        logger.warning("[next_data] HTML parsing failed, returning raw text: %s", exc)
        return body.strip()


# ── Public entry point ────────────────────────────────────────────────────────


def extract_text_from_next_data(html: str, url: str) -> Optional[str]:
    """Attempt to extract article plain text from ``__NEXT_DATA__``.

    Returns plain text on success, or ``None`` when:
    - The page has no ``__NEXT_DATA__`` tag (non-Next.js site).
    - No configured path resolves to a non-empty string.

    The caller should fall back to trafilatura / readability on ``None``.
    """
    data = extract_next_data(html)
    if data is None:
        return None

    domain = _domain_root(url)
    # Domain-specific paths first, then generic fallbacks.
    candidates = _DOMAIN_PATHS.get(domain, []) + _GENERIC_PATHS

    for path in candidates:
        raw = _resolve_path(data, path)
        if raw:
            # Only invoke BeautifulSoup when the value contains actual HTML tags.
            # A bare "<" (e.g. "price < $100") is not sufficient evidence.
            text = _html_to_text(raw) if _HTML_TAG_RE.search(raw) else raw
            logger.debug("[next_data] Extracted via path '%s' for %s", path, domain)
            return text

    logger.debug("[next_data] No matching path found in __NEXT_DATA__ for %s", domain)
    return None
