"""Lightweight Redis-backed diagnostics for worker lanes."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Any

from app.core.dependencies import get_redis
from app.core.logging import get_logger
from app.core.observability import get_process_runtime_stats

logger = get_logger(__name__)

LANE_SET_KEY = "worker:lanes"
LANE_KEY_PREFIX = "worker:lane:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_soft_limit_mb() -> int:
    try:
        return max(256, int(os.getenv("WORKER_MEMORY_SOFT_LIMIT_MB", "1400")))
    except ValueError:
        return 1400


def _memory_snapshot() -> dict[str, Any]:
    try:
        stats = get_process_runtime_stats()
    except Exception:
        stats = {}
    worker_memory_mb = stats.get("container_memory_mb")
    if worker_memory_mb is None:
        worker_memory_mb = stats.get("rss_mb")
    soft_limit_mb = _memory_soft_limit_mb()
    return {
        "memory_mb": worker_memory_mb,
        "rss_mb": stats.get("rss_mb"),
        "soft_limit_mb": soft_limit_mb,
        "is_memory_throttled": (
            worker_memory_mb is not None and float(worker_memory_mb) >= soft_limit_mb
        ),
    }


def record_lane_heartbeat(
    lane: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
    ttl_seconds: int = 300,
) -> None:
    """Persist best-effort heartbeat state for one lane."""
    lane_name = (lane or "unknown").strip() or "unknown"
    updated_at = _now_iso()
    memory = _memory_snapshot()
    last_processed_at = None
    last_throttle_reason = None
    throttle_started_at = None
    previous_payload: dict[str, Any] = {}
    redis_client = None

    try:
        redis_client = get_redis()
        raw_previous = redis_client.get(f"{LANE_KEY_PREFIX}{lane_name}")
        if raw_previous is not None:
            previous_text = (
                raw_previous.decode("utf-8", errors="replace")
                if isinstance(raw_previous, (bytes, bytearray))
                else str(raw_previous)
            )
            previous_payload = json.loads(previous_text)
    except Exception:
        previous_payload = {}

    detail_payload = details or {}
    processed_count = int(detail_payload.get("processed") or 0)
    if processed_count > 0:
        last_processed_at = updated_at
    else:
        last_processed_at = previous_payload.get("last_processed_at")

    if status == "throttled":
        last_throttle_reason = str(detail_payload.get("reason") or "memory_pressure")
    else:
        last_throttle_reason = previous_payload.get("last_throttle_reason")
    is_throttled = bool(memory.get("is_memory_throttled")) or status == "throttled"
    if is_throttled:
        throttle_started_at = previous_payload.get("throttle_started_at") or updated_at

    payload = {
        "lane": lane_name,
        "status": (status or "unknown").strip() or "unknown",
        "updated_at": updated_at,
        "hostname": socket.gethostname(),
        "details": detail_payload,
        **memory,
        "last_processed_at": last_processed_at,
        "last_throttle_reason": last_throttle_reason,
        "throttle_started_at": throttle_started_at,
    }
    try:
        if redis_client is None:
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
