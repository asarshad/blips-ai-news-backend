"""Quota budgeting and lockout controls for YouTube Data API usage."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

logger = get_logger(__name__)

_PT = ZoneInfo("America/Los_Angeles")
_LOCAL_LOCK = threading.Lock()
_LOCAL_STORE: dict[str, tuple[str, Optional[float]]] = {}

_RESERVE_LUA = """
local units = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
for i, key in ipairs(KEYS) do
  local limit = tonumber(ARGV[i + 2])
  local current = tonumber(redis.call('get', key) or '0')
  if current + units > limit then
    return 0
  end
end
for _, key in ipairs(KEYS) do
  redis.call('incrby', key, units)
  redis.call('expire', key, ttl)
end
return 1
"""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until_pacific_reset(now: datetime | None = None) -> int:
    current = (now or _now_utc()).astimezone(_PT)
    next_midnight = datetime.combine(
        current.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=_PT,
    )
    return max(60, int((next_midnight - current).total_seconds()) + 5)


def _quota_day_token(now: datetime | None = None) -> str:
    return (now or _now_utc()).astimezone(_PT).strftime("%Y%m%d")


def _local_cleanup(key: str, *, now_ts: float | None = None) -> None:
    value = _LOCAL_STORE.get(key)
    if value is None:
        return
    _, expires_at = value
    current_ts = now_ts if now_ts is not None else _now_utc().timestamp()
    if expires_at is not None and expires_at <= current_ts:
        _LOCAL_STORE.pop(key, None)


class YouTubeQuotaBudget:
    """Distributed-ish daily budget and cooldown guardrail for YouTube API usage."""

    def __init__(
        self,
        *,
        redis_client=None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.redis = redis_client or self._get_redis_client()
        self._now_provider = now_provider or _now_utc
        self.daily_budget_units = max(0, int(os.getenv("YOUTUBE_API_DAILY_BUDGET_UNITS", "3000")))
        self.search_budget_units = max(
            0,
            min(
                self.daily_budget_units,
                int(os.getenv("YOUTUBE_API_SEARCH_DAILY_BUDGET_UNITS", "1800")),
            ),
        )
        self.duration_budget_units = max(
            0,
            min(
                self.daily_budget_units,
                int(os.getenv("YOUTUBE_API_DURATION_DAILY_BUDGET_UNITS", "600")),
            ),
        )
        self.search_cooldown_minutes = max(
            0, int(os.getenv("YOUTUBE_SEARCH_MIN_INTERVAL_MINUTES", "180"))
        )

    def try_reserve(self, units: int, *, bucket: str = "general") -> bool:
        """Reserve budget units conservatively before issuing a request."""
        if units <= 0:
            return True
        if self.is_locked_out():
            return False

        keys = [self._total_key()]
        limits = [self.daily_budget_units]
        if bucket == "search":
            if self.search_budget_units <= 0:
                return False
            keys.append(self._bucket_key("search"))
            limits.append(self.search_budget_units)
        elif bucket == "duration":
            if self.duration_budget_units <= 0:
                return False
            keys.append(self._bucket_key("duration"))
            limits.append(self.duration_budget_units)

        ttl = _seconds_until_pacific_reset(self._now_provider())
        if self.redis is not None:
            try:
                reserved = self.redis.eval(
                    _RESERVE_LUA,
                    len(keys),
                    *keys,
                    units,
                    ttl,
                    *limits,
                )
                return bool(int(reserved or 0))
            except Exception as exc:
                logger.warning("YouTube quota redis reserve failed; falling back local: %s", exc)

        with _LOCAL_LOCK:
            current_values = []
            now_ts = self._now_provider().timestamp()
            for key, limit in zip(keys, limits, strict=True):
                _local_cleanup(key, now_ts=now_ts)
                current = int((_LOCAL_STORE.get(key) or ("0", None))[0])
                if current + units > int(limit):
                    return False
                current_values.append((key, current))

            expires_at = self._now_provider().timestamp() + ttl
            for key, current in current_values:
                _LOCAL_STORE[key] = (str(current + units), expires_at)
        return True

    def remaining_units(self, *, bucket: str = "all") -> int:
        if bucket == "search":
            return max(0, self.search_budget_units - self._read_counter(self._bucket_key("search")))
        if bucket == "duration":
            return max(
                0, self.duration_budget_units - self._read_counter(self._bucket_key("duration"))
            )
        return max(0, self.daily_budget_units - self._read_counter(self._total_key()))

    def is_locked_out(self) -> bool:
        key = self._lockout_key()
        if self.redis is not None:
            try:
                return bool(self.redis.exists(key))
            except Exception as exc:
                logger.warning(
                    "YouTube quota redis lockout check failed; falling back local: %s", exc
                )

        with _LOCAL_LOCK:
            _local_cleanup(key, now_ts=self._now_provider().timestamp())
            return key in _LOCAL_STORE

    def lock_out_until_reset(self, *, reason: str = "quotaExceeded") -> None:
        ttl = _seconds_until_pacific_reset(self._now_provider())
        key = self._lockout_key()
        if self.redis is not None:
            try:
                self.redis.set(key, reason, nx=False, ex=ttl)
                return
            except Exception as exc:
                logger.warning(
                    "YouTube quota redis lockout write failed; falling back local: %s", exc
                )

        with _LOCAL_LOCK:
            _LOCAL_STORE[key] = (reason, self._now_provider().timestamp() + ttl)

    def begin_search_window(self, surface: str) -> bool:
        """Enforce a minimum interval between expensive search sweeps per surface."""
        if self.search_cooldown_minutes <= 0:
            return True
        if self.is_locked_out():
            return False

        key = self._search_cooldown_key(surface)
        ttl = self.search_cooldown_minutes * 60
        if self.redis is not None:
            try:
                return bool(self.redis.set(key, "1", nx=True, ex=ttl))
            except Exception as exc:
                logger.warning(
                    "YouTube quota redis search cooldown failed; falling back local: %s",
                    exc,
                )

        with _LOCAL_LOCK:
            _local_cleanup(key, now_ts=self._now_provider().timestamp())
            if key in _LOCAL_STORE:
                return False
            _LOCAL_STORE[key] = ("1", self._now_provider().timestamp() + ttl)
            return True

    def _read_counter(self, key: str) -> int:
        if self.redis is not None:
            try:
                value = self.redis.get(key)
                return int(value or 0)
            except Exception as exc:
                logger.warning("YouTube quota redis read failed; falling back local: %s", exc)

        with _LOCAL_LOCK:
            _local_cleanup(key, now_ts=self._now_provider().timestamp())
            return int((_LOCAL_STORE.get(key) or ("0", None))[0])

    def _total_key(self) -> str:
        return f"blips:youtube:quota:{_quota_day_token(self._now_provider())}:total"

    def _bucket_key(self, bucket: str) -> str:
        return f"blips:youtube:quota:{_quota_day_token(self._now_provider())}:{bucket}"

    def _lockout_key(self) -> str:
        return "blips:youtube:quota:lockout"

    def _search_cooldown_key(self, surface: str) -> str:
        return f"blips:youtube:search:cooldown:{surface}"

    def _get_redis_client(self):
        try:
            from app.core.dependencies import get_redis

            return get_redis()
        except Exception:
            return None
