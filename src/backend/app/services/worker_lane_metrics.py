"""Lightweight Redis-backed diagnostics for worker lanes."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any

from app.core.dependencies import get_redis
from app.core.logging import get_logger

logger = get_logger(__name__)

LANE_SET_KEY = "worker:lanes"
LANE_KEY_PREFIX = "worker:lane:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_lane_heartbeat(
    lane: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
    ttl_seconds: int = 300,
) -> None:
    """Persist best-effort heartbeat state for one lane."""
    lane_name = (lane or "unknown").strip() or "unknown"
    payload = {
        "lane": lane_name,
        "status": (status or "unknown").strip() or "unknown",
        "updated_at": _now_iso(),
        "hostname": socket.gethostname(),
        "details": details or {},
    }
    try:
        redis_client = get_redis()
        redis_client.sadd(LANE_SET_KEY, lane_name)
        redis_client.setex(
            f"{LANE_KEY_PREFIX}{lane_name}",
            max(30, int(ttl_seconds)),
            json.dumps(payload, sort_keys=True),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to record worker lane heartbeat for %s: %s", lane_name, exc)


def read_lane_heartbeats() -> dict[str, Any]:
    """Return known lane heartbeat payloads for admin diagnostics."""
    try:
        redis_client = get_redis()
        raw_lanes = redis_client.smembers(LANE_SET_KEY) or set()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc), "lanes": []}

    lanes: list[dict[str, Any]] = []
    for raw_lane in sorted(raw_lanes):
        lane = (
            raw_lane.decode("utf-8", errors="replace")
            if isinstance(raw_lane, (bytes, bytearray))
            else str(raw_lane)
        )
        try:
            raw_payload = redis_client.get(f"{LANE_KEY_PREFIX}{lane}")
            if raw_payload is None:
                lanes.append({"lane": lane, "status": "stale", "updated_at": None})
                continue
            payload_text = (
                raw_payload.decode("utf-8", errors="replace")
                if isinstance(raw_payload, (bytes, bytearray))
                else str(raw_payload)
            )
            lanes.append(json.loads(payload_text))
        except Exception as exc:  # noqa: BLE001
            lanes.append({"lane": lane, "status": "unknown", "error": str(exc)})

    return {"available": True, "lanes": lanes}

