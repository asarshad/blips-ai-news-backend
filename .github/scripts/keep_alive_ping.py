#!/usr/bin/env python3
"""Keep-alive pinger for Render free-tier services.

Designed for GitHub Actions: run on a 5-minute schedule, then perform several
pings within the job (e.g., every ~45s for ~6 minutes).

Hard requirements:
- Avoid spam (sensible bounds + jitter)
- Prefer HEAD, fall back to GET
- Retry transient failures with small backoff
- Do not log secrets (never print query params)
- Exit 0 by default; FAIL_ON_ERROR=true to fail

Env vars:
- HEALTH_URL (required to actually ping)
- INTERVAL_SECONDS (default: 45)
- DURATION_SECONDS (default: 360)
- TIMEOUT_SECONDS (default: 10)
- FAIL_ON_ERROR (default: false)
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests


@dataclass(frozen=True)
class Config:
    health_url: str
    interval_seconds: float
    duration_seconds: float
    timeout_seconds: float
    fail_on_error: bool


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _sanitize_url(url: str) -> str:
    """Return scheme://host/path only (no query/fragment)."""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme or "https"
        host = parsed.netloc or parsed.path  # handles urls like example.com
        path = parsed.path if parsed.netloc else ""
        if not path:
            path = "/"
        return f"{scheme}://{host}{path}"
    except Exception:
        return "<redacted>"


def _clamp(name: str, value: float, *, min_value: float, max_value: float) -> float:
    if value < min_value:
        print(f"WARN: {name}={value} is too low; clamping to {min_value}")
        return min_value
    if value > max_value:
        print(f"WARN: {name}={value} is too high; clamping to {max_value}")
        return max_value
    return value


def load_config() -> Config:
    health_url = (os.getenv("HEALTH_URL") or "").strip()

    interval_seconds = _env_float("INTERVAL_SECONDS", 45.0)
    duration_seconds = _env_float("DURATION_SECONDS", 360.0)
    timeout_seconds = _env_float("TIMEOUT_SECONDS", 10.0)
    fail_on_error = _env_bool("FAIL_ON_ERROR", False)

    # Guardrails
    interval_seconds = _clamp("INTERVAL_SECONDS", interval_seconds, min_value=20.0, max_value=120.0)
    duration_seconds = _clamp("DURATION_SECONDS", duration_seconds, min_value=60.0, max_value=600.0)
    timeout_seconds = _clamp("TIMEOUT_SECONDS", timeout_seconds, min_value=2.0, max_value=30.0)

    return Config(
        health_url=health_url,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
        timeout_seconds=timeout_seconds,
        fail_on_error=fail_on_error,
    )


def _request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout_seconds: float,
    attempts: int = 3,
) -> Optional[int]:
    backoff = 1.0
    for attempt in range(1, attempts + 1):
        try:
            resp = session.request(method, url, timeout=timeout_seconds, allow_redirects=True)
            return int(resp.status_code)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == attempts:
                return None
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 4.0)
        except Exception:
            # Unknown error; don't spin.
            return None
    return None


def ping_once(session: requests.Session, cfg: Config) -> tuple[bool, str]:
    url = cfg.health_url
    safe = _sanitize_url(url)

    status = _request_with_retries(session, "HEAD", url, timeout_seconds=cfg.timeout_seconds)
    if status in (405, 501) or status is None:
        status = _request_with_retries(session, "GET", url, timeout_seconds=cfg.timeout_seconds)

    if status is None:
        return False, f"status=ERR url={safe}"

    ok = 200 <= status < 400
    return ok, f"status={status} url={safe}"


def main() -> int:
    cfg = load_config()

    if not cfg.health_url:
        msg = "HEALTH_URL is not set; skipping keep-alive pings."
        if cfg.fail_on_error:
            print(f"ERROR: {msg}")
            return 2
        print(f"WARN: {msg}")
        return 0

    start = time.monotonic()
    deadline = start + cfg.duration_seconds

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "blips-keepalive/1.0 (+https://github.com/asarshad/blips-ai-news-backend)",
            "Accept": "application/json,text/plain,*/*",
        }
    )

    total = 0
    failures = 0

    print(
        "Keep-alive start: "
        f"interval={cfg.interval_seconds}s duration={cfg.duration_seconds}s timeout={cfg.timeout_seconds}s "
        f"fail_on_error={str(cfg.fail_on_error).lower()}"
    )

    while True:
        now = time.monotonic()
        if now >= deadline:
            break

        total += 1
        ok, line = ping_once(session, cfg)
        if ok:
            print(f"PING {total}: OK {line}")
        else:
            failures += 1
            print(f"PING {total}: FAIL {line}")

        # Sleep to next tick (with jitter) unless we'd overshoot.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        jitter = random.uniform(-2.0, 2.0)
        sleep_for = max(0.0, cfg.interval_seconds + jitter)
        sleep_for = min(sleep_for, remaining)
        time.sleep(sleep_for)

    print(f"Keep-alive done: pings={total} failures={failures}")

    if failures and cfg.fail_on_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
