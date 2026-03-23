"""URL normalization, text cleanup, and validation helpers.

Functions:
- validate_image_url:  reject invalid/relative/data/blob URLs → valid absolute or None
- is_suspicious_image_url: flag low-trust image-like URLs for repair/verification
- make_absolute_url:   resolve relative URL against a base
- clean_text:          strip boilerplate, normalize whitespace
- is_good_text:        heuristic quality check
- compute_text_quality_score: 0..1 quality metric
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

# ── Image URL validation ──────────────────────────────────────────────────────

_INVALID_IMAGE_PREFIXES = ("data:", "blob:", "javascript:", "about:")
_VALID_SCHEMES = {"http", "https"}
_TRACKING_IMAGE_HOST_KEYWORDS = (
    "google-analytics.com",
    "analytics.google.com",
    "googletagmanager.com",
    "doubleclick.net",
)
_TRACKING_IMAGE_PATH_KEYWORDS = (
    "/collect",
    "/g/collect",
    "/r/collect",
    "/pixel",
    "/beacon",
)
_TRACKING_IMAGE_QUERY_KEYS = {
    "tid",
    "cid",
    "gclid",
    "fbclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
}
_SUSPICIOUS_IMAGE_PATH_KEYWORDS = (
    "/analytics",
    "/tracking",
    "/pixel",
    "/beacon",
    "/event",
    "/events",
    "/metrics",
)
_SUSPICIOUS_IMAGE_QUERY_KEYS = {
    "tid",
    "cid",
    "event",
    "event_name",
    "measurement_id",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}


def validate_image_url(url: Optional[str]) -> Optional[str]:
    """Return a valid absolute image URL or None.

    Rejects:
    - None, empty, whitespace-only strings
    - data:, blob:, javascript: URIs
    - Relative paths (no scheme + netloc)
    - Non-http(s) schemes
    - Known tracker/beacon URLs mistakenly exposed as image candidates
    """
    if not url or not url.strip():
        return None

    url = url.strip()

    lowered = url.lower()
    for prefix in _INVALID_IMAGE_PREFIXES:
        if lowered.startswith(prefix):
            return None

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if not scheme or not parsed.netloc:
        return None

    if scheme not in _VALID_SCHEMES:
        return None

    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query_keys = {key.lower() for key in parse_qs(parsed.query).keys()}

    if any(keyword in host for keyword in _TRACKING_IMAGE_HOST_KEYWORDS):
        return None
    if any(
        keyword in path for keyword in _TRACKING_IMAGE_PATH_KEYWORDS
    ) and query_keys.intersection(_TRACKING_IMAGE_QUERY_KEYS):
        return None

    # SSRF guard: reject private/loopback/link-local hosts
    if host:
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                addr = info[4][0]
                try:
                    ip = ipaddress.ip_address(addr)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        return None
                except ValueError:
                    continue
        except OSError:
            pass  # DNS failure is fine here — allow and let image load fail naturally

    return url


def is_suspicious_image_url(url: Optional[str]) -> bool:
    """Return True when a candidate URL looks image-adjacent but low-trust."""
    if not url or not url.strip():
        return False

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query_keys = {key.lower() for key in parse_qs(parsed.query).keys()}

    if any(keyword in host for keyword in _TRACKING_IMAGE_HOST_KEYWORDS):
        return True
    if any(keyword in path for keyword in _SUSPICIOUS_IMAGE_PATH_KEYWORDS):
        return True
    if query_keys.intersection(_SUSPICIOUS_IMAGE_QUERY_KEYS) and not re.search(
        r"\.(jpg|jpeg|png|webp|gif|avif)$", path
    ):
        return True
    return False


def make_absolute_url(url: Optional[str], base_url: str) -> Optional[str]:
    """Resolve *url* against *base_url*, returning an absolute URL.

    Returns None if the input is empty or resolution yields something invalid.
    """
    if not url or not url.strip():
        return None

    url = url.strip()

    # Already absolute?
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url

    # Protocol-relative
    if url.startswith("//"):
        return f"https:{url}"

    try:
        resolved = urljoin(base_url, url)
        rp = urlparse(resolved)
        if rp.scheme in _VALID_SCHEMES and rp.netloc:
            return resolved
    except Exception:
        pass

    return None


# ── Text cleanup ──────────────────────────────────────────────────────────────

_BOILERPLATE_PATTERNS = [
    re.compile(r"subscribe\s+(to\s+)?our\s+newsletter", re.I),
    re.compile(r"sign\s+up\s+for\s+(our\s+)?newsletter", re.I),
    re.compile(r"follow\s+us\s+on\s+(twitter|facebook|instagram|x\.com)", re.I),
    re.compile(r"read\s+more\s+at\s+", re.I),
    re.compile(r"click\s+here\s+to\s+", re.I),
    re.compile(r"share\s+this\s+(article|post)", re.I),
    re.compile(r"(©|copyright)\s*\d{4}", re.I),
    re.compile(r"all\s+rights\s+reserved", re.I),
    re.compile(r"cookie\s+(policy|consent|preferences)", re.I),
    re.compile(r"accept\s+cookies", re.I),
    re.compile(r"privacy\s+policy", re.I),
    re.compile(r"terms\s+(of\s+)?(service|use)", re.I),
]

_MULTI_WHITESPACE = re.compile(r"\s+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_text(text: Optional[str]) -> str:
    """Normalize whitespace and strip common boilerplate lines."""
    if not text:
        return ""

    # Normalize whitespace within lines, preserve paragraph breaks
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        # Skip lines that are mostly boilerplate
        if _is_boilerplate_line(line):
            continue
        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    result = _MULTI_NEWLINE.sub("\n\n", result)
    return result.strip()


def _is_boilerplate_line(line: str) -> bool:
    """Check if a line matches common boilerplate patterns.

    For 'privacy policy' and 'terms of service' patterns we only strip
    short lines (≤ 8 words) because these phrases legitimately appear
    inside editorial sentences in real articles.
    """
    _WORD_GUARDED = {"privacy", "terms"}
    for pat in _BOILERPLATE_PATTERNS:
        if pat.search(line):
            # Check if this pattern needs the word-count guard
            src = pat.pattern
            if any(kw in src for kw in (r"privacy\s+policy", r"terms\s+")):
                if len(line.split()) > 8:
                    continue  # Likely editorial — preserve the line
            return True
    return False


# ── Text quality heuristics ──────────────────────────────────────────────────

_MIN_WORD_COUNT = 100  # Reasonably short tech articles
_IDEAL_WORD_COUNT = 300  # A full article


def word_count(text: str) -> int:
    """Count words in text."""
    if not text:
        return 0
    return len(text.split())


def is_good_text(text: Optional[str], min_words: int = _MIN_WORD_COUNT) -> bool:
    """Heuristic check: is this text good enough for summarization?

    Checks:
    - At least *min_words* words
    - Reasonable ratio of alphanumeric characters
    - Not dominated by nav/boilerplate keywords
    """
    if not text:
        return False

    wc = word_count(text)
    if wc < min_words:
        return False

    # Check alphanumeric ratio
    alnum = sum(1 for c in text if c.isalnum())
    total = len(text)
    if total > 0 and alnum / total < 0.4:
        return False

    # Check for boilerplate domination
    boilerplate_matches = sum(1 for pat in _BOILERPLATE_PATTERNS if pat.search(text))
    if boilerplate_matches > 5:
        return False

    return True


def compute_text_quality_score(text: Optional[str]) -> float:
    """Compute a 0.0–1.0 quality score for extracted text.

    Factors:
    - Word count (0.0 at 0 words, 1.0 at IDEAL_WORD_COUNT+)
    - Alphanumeric ratio
    - Low boilerplate presence
    """
    if not text:
        return 0.0

    wc = word_count(text)
    # Word count factor: ramps from 0→1 over 0→IDEAL range
    wc_score = min(1.0, wc / _IDEAL_WORD_COUNT) if _IDEAL_WORD_COUNT > 0 else 0.0

    # Alphanumeric ratio factor
    alnum = sum(1 for c in text if c.isalnum())
    total = len(text)
    alnum_score = (alnum / total) if total > 0 else 0.0
    alnum_score = min(1.0, alnum_score / 0.6)  # normalize so 0.6 ratio → 1.0

    # Boilerplate penalty
    boilerplate_matches = sum(1 for pat in _BOILERPLATE_PATTERNS if pat.search(text))
    boilerplate_penalty = max(0.0, 1.0 - boilerplate_matches * 0.1)

    return round(wc_score * 0.5 + alnum_score * 0.3 + boilerplate_penalty * 0.2, 3)

    # Boilerplate penalty
    boilerplate_matches = sum(1 for pat in _BOILERPLATE_PATTERNS if pat.search(text))
    boilerplate_penalty = max(0.0, 1.0 - boilerplate_matches * 0.1)

    return round(wc_score * 0.5 + alnum_score * 0.3 + boilerplate_penalty * 0.2, 3)
