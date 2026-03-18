"""Redis-backed planner state for YouTube discovery search rotation."""

from __future__ import annotations

import json
from typing import Any


class DiscoveryPlannerStateStore:
    """Small lazy Redis wrapper for discovery planner state."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client

    def is_available(self) -> bool:
        return self._client() is not None

    def get_json(self, key: str) -> dict[str, Any] | None:
        payload = self.get_text(key)
        if not payload:
            return None
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        self.set_text(key, json.dumps(value))

    def get_text(self, key: str) -> str | None:
        client = self._client()
        if client is None:
            return None
        try:
            payload = client.get(key)
        except Exception:
            return None
        if payload is None:
            return None
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        return str(payload)

    def set_text(self, key: str, value: str) -> None:
        client = self._client()
        if client is None:
            return
        try:
            client.set(key, value)
        except Exception:
            return

    def incr(self, key: str) -> int | None:
        client = self._client()
        if client is None:
            return None
        try:
            return int(client.incr(key))
        except Exception:
            return None

    def _client(self):
        if self._redis is not None:
            return self._redis
        try:
            from app.core.dependencies import get_redis

            self._redis = get_redis()
        except Exception:
            return None
        return self._redis
