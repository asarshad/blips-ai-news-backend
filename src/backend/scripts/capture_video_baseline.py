"""Capture a committed video/reels baseline snapshot for admin comparison."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List

from fastapi.testclient import TestClient

os.environ.setdefault("SKIP_STARTUP_CHECKS", "true")

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def _fetch(client: TestClient, path: str) -> Dict[str, Any]:
    headers = {"X-Admin-Key": settings.ADMIN_API_KEY} if settings.ADMIN_API_KEY else {}
    try:
        response = client.get(path, headers=headers)
        payload: Dict[str, Any]
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text[:1000]}
        return {
            "path": path,
            "status_code": response.status_code,
            "payload": payload,
        }
    except Exception as exc:
        return {
            "path": path,
            "status_code": None,
            "error": str(exc),
        }


def _sample_metrics(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    materialized = list(items)
    ages = [
        round((item.get("published_age_seconds") or 0) / 3600, 2)
        for item in materialized
        if item.get("published_age_seconds") is not None
    ]
    channels = [item.get("channel_id") or item.get("source") or "unknown" for item in materialized]
    dominant_count = max((channels.count(channel) for channel in set(channels)), default=0)
    return {
        "fresh_inventory_24h": len(materialized),
        "median_age_top20_hours": round(median(ages), 2) if ages else None,
        "p95_age_top20_hours": max(ages) if ages else None,
        "distinct_active_channels_24h": len(set(channels)),
        "dominant_channel_pct_top20": round(dominant_count / max(len(materialized), 1) * 100, 2),
        "official_share_top20": None,
        "empty_feed_rate": None,
        "caught_up_rate": None,
        "clickbait_rejection_rate": None,
        "duplicate_rejection_rate": None,
    }


def _items_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("payload") or {}
    items = data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    videos = data.get("videos")
    if isinstance(videos, list):
        return [item for item in videos if isinstance(item, dict)]
    return []


def build_snapshot(tag: str) -> Dict[str, Any]:
    client = TestClient(app)
    raw = {
        "inventory_health": _fetch(client, "/api/v1/metrics/inventory/health"),
        "sources": _fetch(client, "/api/v1/metrics/sources"),
        "signal": _fetch(client, "/api/v1/metrics/signal"),
        "videos_recent": _fetch(client, "/api/v1/videos/recent?limit=20&page=1"),
        "videos_reels": _fetch(client, "/api/v1/videos/reels?limit=20&page=1"),
    }

    recent_items = _items_from_payload(raw["videos_recent"])
    reel_items = _items_from_payload(raw["videos_reels"])
    capture_errors = [entry.get("error") for entry in raw.values() if entry.get("error")]

    return {
        "tag": tag,
        "captured_at": datetime.utcnow().isoformat(),
        "capture_status": "ok" if not capture_errors else "partial",
        "capture_error": "; ".join(capture_errors) if capture_errors else None,
        "video_supply": {
            "videos": _sample_metrics(recent_items),
            "reels": _sample_metrics(reel_items),
        },
        "samples": {
            "videos_recent": recent_items,
            "videos_reels": reel_items,
        },
        "raw": raw,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    snapshot = build_snapshot(args.tag)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
