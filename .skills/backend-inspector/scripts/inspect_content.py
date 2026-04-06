#!/usr/bin/env python3
"""Inspect one content item through admin, public, and fresh source metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.article_hydration import ArticleHydrationService
from app.extraction.fetcher import fetch_url
from app.extraction.metadata import extract_metadata, is_probably_generic_image_url
from app.extraction.normalize import is_suspicious_image_url


DEFAULT_BASE_URL = os.environ.get("BLIPS_BASE_URL", "https://blips-api.onrender.com")
DEFAULT_APP_VERSION = "codex-backend-inspector"
DEFAULT_PLATFORM = "ios"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("content_id", type=int, help="Content item ID to inspect.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL.")
    parser.add_argument(
        "--admin-key",
        default=os.environ.get("BLIPS_ADMIN_KEY", ""),
        help="Admin API key. Falls back to BLIPS_ADMIN_KEY.",
    )
    parser.add_argument(
        "--skip-source-fetch",
        action="store_true",
        help="Skip the fresh source-page fetch/extraction pass.",
    )
    return parser.parse_args()


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    encoded = None
    request_headers = dict(headers or {})
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return response.status, dict(response.headers.items()), json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        parsed: Any = payload
        try:
            parsed = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            pass
        return exc.code, dict(exc.headers.items()), parsed


def _bootstrap_session(base_url: str) -> dict[str, Any]:
    status, _, payload = _request_json(
        base_url,
        "/api/v1/auth/session",
        method="POST",
        body={"platform": DEFAULT_PLATFORM, "app_version": DEFAULT_APP_VERSION},
    )
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError(f"Session bootstrap failed with status {status}: {payload}")
    return payload


def _summarize_image(url: str | None) -> dict[str, Any]:
    normalized = ArticleHydrationService.normalize_article_image(url)
    return {
        "url": normalized,
        "generic": is_probably_generic_image_url(normalized),
        "suspicious": is_suspicious_image_url(normalized),
    }


def _fetch_fresh_metadata(source_url: str) -> dict[str, Any]:
    fetch = fetch_url(source_url)
    result: dict[str, Any] = {
        "fetch_url": fetch.url or source_url,
        "status_code": fetch.status_code,
        "content_type": fetch.content_type,
        "error": fetch.error,
    }
    if fetch.error or not fetch.html:
        return result

    meta = extract_metadata(fetch.html, fetch.url or source_url)
    result["metadata"] = {
        "canonical_url": meta.canonical_url,
        "title": meta.title,
        "description": meta.description,
        "image_url": meta.image_url,
        "image_source": meta.image_source,
        "image_confidence": meta.image_confidence,
        "image_suspicious": meta.image_suspicious,
        "published_at_str": meta.published_at_str,
    }
    result["normalized_image"] = _summarize_image(meta.image_url)
    return result


def main() -> int:
    args = parse_args()
    if not args.admin_key:
        raise SystemExit("BLIPS_ADMIN_KEY or --admin-key is required for admin inspection")

    admin_headers = {"X-Admin-Key": args.admin_key}
    admin_status, _, admin_payload = _request_json(
        args.base_url,
        f"/api/v1/admin/editorial/content/{args.content_id}",
        headers=admin_headers,
    )

    report: dict[str, Any] = {
        "base_url": args.base_url,
        "content_id": args.content_id,
        "admin_status": admin_status,
        "admin_payload": admin_payload,
    }

    if admin_status != 200 or not isinstance(admin_payload, dict):
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    if admin_payload.get("content_type") == "ARTICLE":
        session = _bootstrap_session(args.base_url)
        public_status, _, public_payload = _request_json(
            args.base_url,
            f"/api/v1/articles/{args.content_id}",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )
        report["public_status"] = public_status
        report["public_payload"] = public_payload

    stored_image = (
        (admin_payload.get("image_url") or "").strip()
        if isinstance(admin_payload, dict)
        else ""
    )
    report["stored_image"] = _summarize_image(stored_image)

    source_url = ""
    if isinstance(admin_payload, dict):
        source_url = (
            (admin_payload.get("canonical_url") or "").strip()
            or (admin_payload.get("source_url") or "").strip()
        )

    if source_url and not args.skip_source_fetch:
        report["fresh_source"] = _fetch_fresh_metadata(source_url)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
