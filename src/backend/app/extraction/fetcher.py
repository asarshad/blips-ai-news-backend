"""Robust HTTP fetcher with retries, rate limiting, and caching headers.

Uses httpx with:
- Configurable connect/read timeouts
- Exponential backoff on transient errors (429, 5xx, timeouts)
- Polite User-Agent
- Per-domain in-memory rate limiting (token bucket)
- Optional ETag / If-Modified-Since support
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
import requests

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# Version-dynamic User-Agent so it stays accurate after httpx upgrades.
USER_AGENT: str = (
    f"BlipsBot/1.0 (+https://blips.dev/bot; content-extraction) httpx/{httpx.__version__}"
)
BROWSER_FALLBACK_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024  # 5 MB byte cap

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_BOT_BLOCK_STATUS = {403, 429}
_REQUESTS_FALLBACK_STATUS = {401, 402, 403, 429}
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_HTML_CONTENT_TYPE_HINTS = (
    "text/html",
    "application/xhtml+xml",
    "application/xml",
)
_PLAIN_TEXT_FALLBACK_HINTS = (
    "text/plain",
    "text/markdown",
)
_BINARY_CONTENT_TYPE_PREFIXES = (
    "audio/",
    "font/",
    "image/",
    "video/",
)
_BINARY_CONTENT_TYPE_HINTS = (
    "application/gzip",
    "application/octet-stream",
    "application/pdf",
    "application/vnd",
    "application/x-gzip",
    "application/x-tar",
    "application/zip",
)
_BOT_PROTECTION_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"just a moment", re.IGNORECASE), "cloudflare_challenge"),
    (re.compile(r"attention required", re.IGNORECASE), "cloudflare_challenge"),
    (re.compile(r"verify you are human", re.IGNORECASE), "human_verification"),
    (re.compile(r"captcha", re.IGNORECASE), "captcha_challenge"),
    (re.compile(r"enable javascript and cookies", re.IGNORECASE), "javascript_cookie_gate"),
    (re.compile(r"access denied", re.IGNORECASE), "access_denied"),
)

# ── Response dataclass ────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    """Result of an HTTP fetch."""

    url: str
    status_code: int = 0
    html: str = ""
    content_type: str = ""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error: Optional[str] = None
    not_modified: bool = False  # 304
    elapsed_ms: float = 0.0


# ── SSRF protection ───────────────────────────────────────────────────────────


def _is_private_host(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback/link-local address.

    Blocks RFC-1918 (10.x, 172.16-31.x, 192.168.x), loopback (127.x),
    link-local (169.254.x / fe80::), and other non-globally-routable ranges.
    """
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return True
            except ValueError:
                continue
    except OSError:
        pass  # DNS failure — let the request fail naturally
    return False


# ── Per-domain rate limiter (in-memory, bounded) ──────────────────────────────

_domain_last_request: "OrderedDict[str, float]" = OrderedDict()
_domain_lock = Lock()
_DOMAIN_CACHE_MAX = 1000  # Evict oldest entries to prevent unbounded growth


def _rate_limit_domain(domain: str, min_interval: float) -> None:
    """Block until the minimum interval for *domain* has elapsed.

    The global lock is released *before* sleeping so that threads waiting
    on different domains are not serialized behind each other.
    """
    with _domain_lock:
        now = time.monotonic()
        last = _domain_last_request.get(domain, 0.0)
        wait = min_interval - (now - last)

    # Sleep *outside* the lock — other domains must not be blocked while waiting
    if wait > 0:
        time.sleep(wait)

    with _domain_lock:
        # Evict oldest entry when cache is at capacity
        while len(_domain_last_request) >= _DOMAIN_CACHE_MAX:
            _domain_last_request.popitem(last=False)
        _domain_last_request[domain] = time.monotonic()


# ── Shared httpx client ──────────────────────────────────────────────────────

_client: Optional[httpx.Client] = None
_client_lock = Lock()


def _build_client() -> httpx.Client:
    """Build a fresh httpx.Client, reading settings at call time."""
    s = get_settings()
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=float(getattr(s, "EXTRACTION_CONNECT_TIMEOUT", 10)),
            read=float(getattr(s, "EXTRACTION_READ_TIMEOUT", 20)),
            write=10.0,
            pool=10.0,
        ),
        follow_redirects=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
        ),
    )


def _get_client() -> httpx.Client:
    """Return the shared httpx.Client, building or rebuilding as needed."""
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = _build_client()
    return _client


def fetch_url(
    url: str,
    *,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FetchResult:
    """Fetch *url* with retries and per-domain rate limiting.

    Returns a ``FetchResult`` — never raises on network/HTTP errors.

    Security: requests to private/loopback/link-local hosts are rejected
    before any connection is made to prevent SSRF attacks.
    """
    parsed = urlparse(url)
    domain = parsed.netloc

    target_error = _validate_fetch_target(url)
    if target_error is not None:
        return FetchResult(url=url, error=target_error)

    safe_url = _safe_url_for_log(url)
    s = get_settings()
    max_retries = int(getattr(s, "EXTRACTION_MAX_RETRIES", 3))
    domain_min_interval = float(getattr(s, "EXTRACTION_DOMAIN_MIN_INTERVAL", 1.0))

    _rate_limit_domain(domain, domain_min_interval)

    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    client = _get_client()
    last_error: Optional[str] = None
    elapsed: float = 0.0  # Always defined — prevents NameError when max_retries=0
    tried_browser_fallback = False

    for attempt in range(1, max_retries + 1):
        t0 = time.monotonic()
        try:
            resp, elapsed = _get_with_validated_redirects(client, url, headers)

            if resp.status_code in _BOT_BLOCK_STATUS and not tried_browser_fallback:
                tried_browser_fallback = True
                browser_headers = _build_browser_fallback_headers(headers)
                fallback_resp, fallback_elapsed = _get_with_validated_redirects(
                    client,
                    url,
                    browser_headers,
                )
                resp = fallback_resp
                elapsed += fallback_elapsed

            if _should_try_requests_browser_fallback(
                resp
            ) and not _response_prefers_browser_variant(resp):
                browser_headers = _build_browser_fallback_headers(headers)
                fallback_resp, fallback_elapsed = _requests_get_with_validated_redirects(
                    url,
                    browser_headers,
                )
                elapsed += fallback_elapsed
                if _response_prefers_browser_variant(fallback_resp):
                    resp = fallback_resp

            if resp.status_code == 304:
                return FetchResult(
                    url=str(resp.url),
                    status_code=304,
                    not_modified=True,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    elapsed_ms=elapsed,
                )

            if protection_reason := _detect_bot_protection_response(resp):
                return FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    content_type=resp.headers.get("Content-Type", ""),
                    error=f"BOT_PROTECTED: {protection_reason}",
                    elapsed_ms=elapsed,
                )

            if resp.status_code in _TRANSIENT_STATUS:
                last_error = f"HTTP {resp.status_code}"
                _backoff(attempt)
                continue

            if resp.status_code >= 400:
                return FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    error=f"HTTP {resp.status_code}",
                    elapsed_ms=elapsed,
                )

            ct = resp.headers.get("Content-Type", "")
            if _is_binary_content_type(ct):
                return FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    content_type=ct,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    elapsed_ms=elapsed,
                )

            raw = _read_response_bytes_limited(resp, max_bytes=MAX_RESPONSE_BYTES)
            encoding = resp.encoding or "utf-8"
            html_text = raw.decode(encoding, errors="replace")

            return FetchResult(
                url=str(resp.url),
                status_code=resp.status_code,
                html=html_text,
                content_type=ct,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                elapsed_ms=elapsed,
            )

        except httpx.TimeoutException as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = f"Timeout: {exc}"
            logger.warning("[fetch] Timeout fetching %s (attempt %s): %s", safe_url, attempt, exc)
            _backoff(attempt)

        except httpx.HTTPError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = f"HTTP error: {exc}"
            logger.warning(
                "[fetch] HTTP error fetching %s (attempt %s): %s", safe_url, attempt, exc
            )
            _backoff(attempt)

        except ValueError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = str(exc)
            logger.warning(
                "[fetch] Request blocked for %s (attempt %s): %s", safe_url, attempt, exc
            )
            break

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = f"Unexpected: {exc}"
            logger.error(
                "[fetch] Unexpected error fetching %s (attempt %s): %s",
                safe_url,
                attempt,
                exc,
            )
            break  # Non-transient — don't retry
        finally:
            try:
                resp.close()
            except Exception:
                pass

    return FetchResult(
        url=url,
        status_code=0,
        error=last_error or "All retries exhausted",
        elapsed_ms=elapsed,
    )


def _backoff(attempt: int) -> None:
    s = get_settings()
    base = float(getattr(s, "EXTRACTION_BACKOFF_BASE", 1.5))
    time.sleep(min(base**attempt, 30.0))


def _safe_url_for_log(url: str, *, max_length: int = 200) -> str:
    """Return a log-safe URL string without dumping large inline payloads."""
    if not url:
        return url

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "data":
        prefix, _, _ = url.partition(",")
        return f"{prefix},<omitted>"

    if len(url) <= max_length:
        return url

    if scheme in {"http", "https"}:
        condensed = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            condensed = f"{condensed}?..."
        if len(condensed) <= max_length:
            return condensed

    return f"{url[: max_length - 3]}..."


def _validate_fetch_target(url: str) -> Optional[str]:
    """Return an SSRF/validation error string for an invalid target, else None."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    safe_url = _safe_url_for_log(url)

    if scheme not in {"http", "https"}:
        logger.warning("[fetch] Unsupported URL scheme blocked: %s", safe_url)
        return f"Unsupported URL scheme {scheme!r}"

    if host and _is_private_host(host):
        logger.warning("[fetch] SSRF blocked: %s resolves to a private address", safe_url)
        return f"SSRF: host {host!r} resolves to a private/internal address"

    return None


def _build_browser_fallback_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return browser-like headers for bot-sensitive sites."""
    browser_headers = dict(headers)
    browser_headers["User-Agent"] = BROWSER_FALLBACK_USER_AGENT
    browser_headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    )
    browser_headers["Accept-Language"] = "en-US,en;q=0.9"
    browser_headers["Upgrade-Insecure-Requests"] = "1"
    return browser_headers


def _is_binary_content_type(content_type: str) -> bool:
    """Return True when the response advertises a binary/non-document payload."""
    lowered = (content_type or "").strip().lower()
    if not lowered:
        return False
    return lowered.startswith(_BINARY_CONTENT_TYPE_PREFIXES) or any(
        hint in lowered for hint in _BINARY_CONTENT_TYPE_HINTS
    )


def _read_response_bytes_limited(response, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` from a response without buffering the whole body."""
    reader = getattr(response, "iter_bytes", None)
    if callable(reader):
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in reader():
                if not chunk:
                    continue
                remaining = max_bytes - total
                if remaining <= 0:
                    break
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    total = max_bytes
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
        except Exception:
            chunks.clear()
        if chunks:
            return b"".join(chunks)

    raw = getattr(response, "content", b"") or b""
    return raw[:max_bytes]


def _should_try_requests_browser_fallback(response: httpx.Response) -> bool:
    """Return True when the shared httpx path likely saw a bot-specific variant."""
    if response.status_code in _REQUESTS_FALLBACK_STATUS:
        return True
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code >= 400:
        return False
    return any(hint in content_type for hint in _PLAIN_TEXT_FALLBACK_HINTS)


def _response_prefers_browser_variant(response) -> bool:
    """Return True when a response looks like the canonical browser-facing document."""
    if response.status_code >= 400:
        return False

    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type:
        if any(hint in content_type for hint in _PLAIN_TEXT_FALLBACK_HINTS):
            return False
        if not any(hint in content_type for hint in _HTML_CONTENT_TYPE_HINTS):
            return False

    raw = getattr(response, "content", b"") or b""
    if not isinstance(raw, (bytes, bytearray)):
        raw = b""
    raw = raw[:4096]
    encoding = response.encoding or "utf-8"
    text = raw.decode(encoding, errors="replace").lstrip().lower()
    if "<html" in text or text.startswith("<!doctype html"):
        return True
    return bool(re.search(r"<meta|<title|<article|<main", text))


def _detect_bot_protection_response(response) -> Optional[str]:
    """Return a protection reason when the response looks like a challenge page."""
    status_code = int(getattr(response, "status_code", 0) or 0)
    headers = getattr(response, "headers", {}) or {}
    server = str(headers.get("Server", "") or "").lower()
    content_type = str(headers.get("Content-Type", "") or "").lower()
    raw = getattr(response, "content", b"") or b""
    if not isinstance(raw, (bytes, bytearray)):
        raw = b""
    encoding = getattr(response, "encoding", None) or "utf-8"
    text = raw[:8192].decode(encoding, errors="replace").lower()

    if "cloudflare" in server and status_code in {401, 403, 429}:
        return "cloudflare_challenge"

    if not text and status_code not in {401, 403, 429}:
        return None

    for pattern, reason in _BOT_PROTECTION_TEXT_PATTERNS:
        if pattern.search(text):
            if status_code in {200, 401, 403, 429, 503} or "html" in content_type:
                return reason

    return None


def _get_with_validated_redirects(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
) -> tuple[httpx.Response, float]:
    """Issue a GET request while validating each redirect hop before following it."""
    current_url = url
    total_elapsed_ms = 0.0

    for _ in range(_MAX_REDIRECTS + 1):
        t0 = time.monotonic()
        request = client.build_request("GET", current_url, headers=headers)
        response = client.send(request, stream=True)
        total_elapsed_ms += (time.monotonic() - t0) * 1000

        if response.status_code not in _REDIRECT_STATUS:
            return response, total_elapsed_ms

        location = response.headers.get("Location")
        if not location:
            return response, total_elapsed_ms

        next_url = urljoin(str(response.url), location)
        target_error = _validate_fetch_target(next_url)
        if target_error is not None:
            response.close()
            raise ValueError(target_error)

        response.close()
        current_url = next_url

    raise ValueError("Too many redirects")


def _requests_get_with_validated_redirects(
    url: str,
    headers: dict[str, str],
) -> tuple[requests.Response, float]:
    """Browser-style fallback fetch with the same redirect validation rules."""
    settings = get_settings()
    connect_timeout = float(getattr(settings, "EXTRACTION_CONNECT_TIMEOUT", 10))
    read_timeout = float(getattr(settings, "EXTRACTION_READ_TIMEOUT", 20))
    current_url = url
    total_elapsed_ms = 0.0

    session = requests.Session()
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            t0 = time.monotonic()
            response = session.get(
                current_url,
                headers=headers,
                timeout=(connect_timeout, read_timeout),
                allow_redirects=False,
            )
            total_elapsed_ms += (time.monotonic() - t0) * 1000

            if response.status_code not in _REDIRECT_STATUS:
                return response, total_elapsed_ms

            location = response.headers.get("Location")
            if not location:
                return response, total_elapsed_ms

            next_url = urljoin(str(response.url), location)
            target_error = _validate_fetch_target(next_url)
            if target_error is not None:
                raise ValueError(target_error)

            current_url = next_url
    finally:
        session.close()

    raise ValueError("Too many redirects")
