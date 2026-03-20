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
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# Version-dynamic User-Agent so it stays accurate after httpx upgrades.
USER_AGENT: str = (
    f"BlipsBot/1.0 (+https://blips.dev/bot; content-extraction) httpx/{httpx.__version__}"
)
MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024  # 5 MB byte cap

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5

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

    for attempt in range(1, max_retries + 1):
        t0 = time.monotonic()
        try:
            resp, elapsed = _get_with_validated_redirects(client, url, headers)

            if resp.status_code == 304:
                return FetchResult(
                    url=url,
                    status_code=304,
                    not_modified=True,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    elapsed_ms=elapsed,
                )

            if resp.status_code in _TRANSIENT_STATUS:
                last_error = f"HTTP {resp.status_code}"
                _backoff(attempt)
                continue

            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    status_code=resp.status_code,
                    error=f"HTTP {resp.status_code}",
                    elapsed_ms=elapsed,
                )

            # Success path — truncate on raw bytes for a real byte-level cap
            raw = resp.content
            if len(raw) > MAX_RESPONSE_BYTES:
                raw = raw[:MAX_RESPONSE_BYTES]
            encoding = resp.encoding or "utf-8"
            html_text = raw.decode(encoding, errors="replace")

            ct = resp.headers.get("Content-Type", "")
            return FetchResult(
                url=url,
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
            logger.warning(f"[fetch] Timeout fetching {url} (attempt {attempt}): {exc}")
            _backoff(attempt)

        except httpx.HTTPError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = f"HTTP error: {exc}"
            logger.warning(f"[fetch] HTTP error fetching {url} (attempt {attempt}): {exc}")
            _backoff(attempt)

        except ValueError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = str(exc)
            logger.warning(f"[fetch] Request blocked for {url} (attempt {attempt}): {exc}")
            break

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            last_error = f"Unexpected: {exc}"
            logger.error(f"[fetch] Unexpected error fetching {url} (attempt {attempt}): {exc}")
            break  # Non-transient — don't retry

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


def _validate_fetch_target(url: str) -> Optional[str]:
    """Return an SSRF/validation error string for an invalid target, else None."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""

    if scheme not in {"http", "https"}:
        logger.warning("[fetch] Unsupported URL scheme blocked: %r", url)
        return f"Unsupported URL scheme {scheme!r}"

    if host and _is_private_host(host):
        logger.warning(f"[fetch] SSRF blocked: {url!r} resolves to a private address")
        return f"SSRF: host {host!r} resolves to a private/internal address"

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
        response = client.get(current_url, headers=headers)
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

    raise ValueError("Too many redirects")
