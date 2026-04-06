#!/usr/bin/env python3
"""Audit recently added article images and surface likely extraction mismatches."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
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
LOW_RES_THRESHOLD = 200 * 200
_DIMENSION_RE = re.compile(r"(?<![a-z])(w|width|h|height)[=_-](\d{1,4})(?!\d)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL.")
    parser.add_argument(
        "--admin-key",
        default=os.environ.get("BLIPS_ADMIN_KEY", ""),
        help="Admin API key. Falls back to BLIPS_ADMIN_KEY.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look back this many hours using created_at/published_at signals.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=100,
        help="Maximum recent article rows to inspect in detail.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size to use against the editorial content endpoint.",
    )
    parser.add_argument(
        "--include-soft-mismatches",
        action="store_true",
        help="Include fresh-image mismatches even when the stored image is not obviously low-trust.",
    )
    return parser.parse_args()


def _request_json(
    base_url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return exc.code, {"raw": payload}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _select_recent_timestamp(item: dict[str, Any]) -> datetime | None:
    created_at = _parse_dt(item.get("created_at"))
    published_at = _parse_dt(item.get("published_at"))
    choices = [dt for dt in (created_at, published_at) if dt is not None]
    return max(choices) if choices else None


def _list_recent_article_summaries(
    *,
    base_url: str,
    admin_key: str,
    hours: int,
    page_size: int,
    max_items: int,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, hours))
    days = sorted({now.date(), cutoff.date(), (cutoff - timedelta(days=1)).date()}, reverse=True)
    headers = {"X-Admin-Key": admin_key}
    items_by_id: dict[int, dict[str, Any]] = {}

    for day in days:
        page = 1
        while len(items_by_id) < max_items:
            query = urllib.parse.urlencode(
                {
                    "day": day.isoformat(),
                    "type": "ARTICLE",
                    "page": page,
                    "page_size": max(1, min(page_size, 200)),
                    "sort_by": "published_at",
                }
            )
            status, payload = _request_json(
                base_url,
                f"/api/v1/admin/editorial/content?{query}",
                headers=headers,
            )
            if status != 200 or not isinstance(payload, dict):
                break
            batch = payload.get("items") or []
            if not isinstance(batch, list) or not batch:
                break

            batch_had_recent = False
            for raw in batch:
                if not isinstance(raw, dict):
                    continue
                recent_ts = _select_recent_timestamp(raw)
                if recent_ts and recent_ts >= cutoff:
                    items_by_id[int(raw["id"])] = raw
                    batch_had_recent = True

            total_pages = int(payload.get("pages") or page)
            if page >= total_pages:
                break
            if not batch_had_recent and day < now.date():
                break
            page += 1

    items = sorted(
        items_by_id.values(),
        key=lambda item: _select_recent_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items[:max_items]


def _get_content_detail(base_url: str, admin_key: str, content_id: int) -> dict[str, Any] | None:
    status, payload = _request_json(
        base_url,
        f"/api/v1/admin/editorial/content/{content_id}",
        headers={"X-Admin-Key": admin_key},
    )
    if status != 200 or not isinstance(payload, dict):
        return None
    return payload


def _extract_dimensions(url: str | None) -> tuple[int | None, int | None]:
    if not url:
        return None, None
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    width: int | None = None
    height: int | None = None
    for key in ("w", "width"):
        values = params.get(key)
        if values:
            try:
                width = int(values[0])
                break
            except (TypeError, ValueError):
                pass
    for key in ("h", "height"):
        values = params.get(key)
        if values:
            try:
                height = int(values[0])
                break
            except (TypeError, ValueError):
                pass
    if width is None or height is None:
        matches = _DIMENSION_RE.findall(url)
        for label, raw_value in matches:
            try:
                value = int(raw_value)
            except ValueError:
                continue
            lowered = label.lower()
            if lowered in {"w", "width"} and width is None:
                width = value
            if lowered in {"h", "height"} and height is None:
                height = value
    return width, height


def _low_res_signal(url: str | None) -> dict[str, Any]:
    width, height = _extract_dimensions(url)
    if width and height:
        return {
            "width": width,
            "height": height,
            "is_low_res": (width * height) <= LOW_RES_THRESHOLD,
        }
    return {"width": width, "height": height, "is_low_res": False}


def _normalized_image(url: str | None) -> str | None:
    return ArticleHydrationService.normalize_article_image(url)


def _image_summary(url: str | None) -> dict[str, Any]:
    normalized = _normalized_image(url)
    low_res = _low_res_signal(normalized)
    return {
        "url": normalized,
        "generic": is_probably_generic_image_url(normalized),
        "suspicious": is_suspicious_image_url(normalized),
        "width": low_res["width"],
        "height": low_res["height"],
        "is_low_res": low_res["is_low_res"],
    }


def _same_image(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    l = urllib.parse.urlparse(left)
    r = urllib.parse.urlparse(right)
    return (
        (l.hostname or "").lower() == (r.hostname or "").lower()
        and (l.path or "").rstrip("/") == (r.path or "").rstrip("/")
    )


def _fresh_metadata(source_url: str) -> dict[str, Any]:
    fetch = fetch_url(source_url)
    result: dict[str, Any] = {
        "fetch_url": fetch.url or source_url,
        "status_code": fetch.status_code,
        "content_type": fetch.content_type,
        "error": fetch.error,
        "metadata": None,
    }
    if fetch.error or not fetch.html:
        return result

    meta = extract_metadata(fetch.html, fetch.url or source_url)
    result["metadata"] = {
        "canonical_url": meta.canonical_url,
        "title": meta.title,
        "image_url": meta.image_url,
        "image_source": meta.image_source,
        "image_confidence": meta.image_confidence,
        "image_suspicious": meta.image_suspicious,
        "published_at_str": meta.published_at_str,
    }
    return result


def _classify_issue(
    *,
    detail: dict[str, Any],
    fresh: dict[str, Any],
    include_soft_mismatches: bool,
) -> tuple[list[str], str]:
    stored = _image_summary(detail.get("image_url"))
    fresh_meta = fresh.get("metadata") or {}
    fresh_image = _image_summary(fresh_meta.get("image_url"))
    reasons: list[str] = []
    severity = "none"

    if not stored["url"]:
        reasons.append("missing_image")
        severity = "hard"
    if stored["generic"]:
        reasons.append("generic_image")
        severity = "hard"
    if stored["suspicious"]:
        reasons.append("suspicious_image")
        severity = "hard"
    if stored["is_low_res"]:
        reasons.append("low_resolution_image")
        severity = "hard"

    if fresh.get("error"):
        if str(fresh["error"]).startswith("BOT_PROTECTED:"):
            reasons.append("bot_protected")
        elif severity == "hard":
            reasons.append("fresh_fetch_failed")
        return reasons, severity

    if not fresh_image["url"]:
        if severity == "hard":
            reasons.append("fresh_extraction_missing")
        return reasons, severity

    if not stored["url"]:
        reasons.append("recoverable_missing_image")
        return reasons, "hard"

    if _same_image(stored["url"], fresh_image["url"]):
        return reasons, severity

    fresh_source = str(fresh_meta.get("image_source") or "")
    fresh_confidence = str(fresh_meta.get("image_confidence") or "")
    if severity == "hard":
        reasons.append("better_fresh_candidate")
        return reasons, "hard"

    if include_soft_mismatches and fresh_source == "body" and fresh_confidence in {"high", "medium"}:
        reasons.append("body_image_mismatch")
        return reasons, "soft"

    return reasons, severity


def main() -> int:
    args = parse_args()
    if not args.admin_key:
        raise SystemExit("BLIPS_ADMIN_KEY or --admin-key is required")

    summaries = _list_recent_article_summaries(
        base_url=args.base_url,
        admin_key=args.admin_key,
        hours=args.hours,
        page_size=args.page_size,
        max_items=args.max_items,
    )

    scanned = 0
    hard_suspects: list[dict[str, Any]] = []
    soft_mismatches: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for item in summaries:
        detail = _get_content_detail(args.base_url, args.admin_key, int(item["id"]))
        if detail is None:
            continue
        scanned += 1

        source_url = (detail.get("canonical_url") or "").strip() or (detail.get("source_url") or "").strip()
        fresh = _fresh_metadata(source_url) if source_url else {"error": "missing_source_url", "metadata": None}
        reasons, severity = _classify_issue(
            detail=detail,
            fresh=fresh,
            include_soft_mismatches=args.include_soft_mismatches,
        )
        if not reasons:
            continue

        for reason in reasons:
            reason_counts[reason] += 1

        row = {
            "id": detail.get("id"),
            "title": detail.get("title"),
            "source": detail.get("source"),
            "source_url": source_url or None,
            "published_at": detail.get("published_at"),
            "created_at": detail.get("created_at"),
            "stored_image": _image_summary(detail.get("image_url")),
            "fresh_fetch": {
                "fetch_url": fresh.get("fetch_url"),
                "status_code": fresh.get("status_code"),
                "content_type": fresh.get("content_type"),
                "error": fresh.get("error"),
            },
            "fresh_metadata": fresh.get("metadata"),
            "reasons": reasons,
        }
        if severity == "hard":
            hard_suspects.append(row)
        elif severity == "soft":
            soft_mismatches.append(row)

    report = {
        "base_url": args.base_url,
        "hours": args.hours,
        "scanned": scanned,
        "hard_suspect_count": len(hard_suspects),
        "soft_mismatch_count": len(soft_mismatches),
        "reason_counts": dict(reason_counts),
        "suggested_targeted_repair_ids": [row["id"] for row in hard_suspects],
        "hard_suspects": hard_suspects,
        "soft_mismatches": soft_mismatches,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
