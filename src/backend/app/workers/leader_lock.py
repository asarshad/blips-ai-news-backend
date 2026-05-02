"""Distributed leader lock for the single Render worker service."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field

from redis.exceptions import RedisError

from app.core.dependencies import get_redis
from app.core.logging import get_logger

logger = get_logger(__name__)

_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "  return redis.call('expire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "  return redis.call('del', KEYS[1]) "
    "else return 0 end"
)


@dataclass
class WorkerLeaderLock:
    """CAS-protected Redis lock used to prevent duplicate worker containers."""

    key: str = field(
        default_factory=lambda: os.getenv("SCHEDULER_LEADER_LOCK_KEY", "scheduler_lock")
    )
    ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "120"))
    )
    token: str = field(default_factory=lambda: f"{os.getpid()}:{uuid.uuid4()}")

    @property
    def refresh_interval_seconds(self) -> int:
        configured = int(os.getenv("SCHEDULER_LOCK_REFRESH_SECONDS", "30"))
        return max(5, min(configured, max(5, self.ttl_seconds - 1)))

    def acquire(self) -> bool | None:
        """Acquire the lock.

        Returns True when acquired, False when another owner exists, and None
        when Redis was temporarily unavailable.
        """
        try:
            redis_client = get_redis()
            acquired = redis_client.set(
                self.key,
                self.token,
                nx=True,
                ex=self.ttl_seconds,
            )
            return bool(acquired)
        except RedisError as exc:
            logger.error("Failed to acquire worker lock: %s", exc)
            return None

    def acquire_with_retry(
        self,
        stop_event: threading.Event,
        *,
        max_attempts: int = 10,
        base_delay_seconds: float = 5.0,
    ) -> bool:
        """Acquire the lock with backoff for cold starts and deploy overlap."""
        for attempt in range(1, max_attempts + 1):
            lock_result = self.acquire()
            if lock_result is True:
                return True

            delay_cap = 60 if lock_result is None else self.ttl_seconds
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), delay_cap)
            if lock_result is None:
                logger.warning(
                    "Redis unavailable during worker lock acquisition "
                    "(attempt %s/%s). Retrying in %.0fs",
                    attempt,
                    max_attempts,
                    delay,
                )
            else:
                logger.warning(
                    "Worker lock held by another process (attempt %s/%s). "
                    "Retrying in %.0fs",
                    attempt,
                    max_attempts,
                    delay,
                )
            if stop_event.wait(timeout=delay):
                return False
        return False

    def refresh(self) -> bool | None:
        """Refresh the lock TTL only if this process still owns it."""
        try:
            redis_client = get_redis()
            result = redis_client.eval(
                _REFRESH_LUA,
                1,
                self.key,
                self.token,
                str(self.ttl_seconds),
            )
            if int(result or 0) == 0:
                logger.warning("Worker lock lost; another process owns it")
                return False
            return True
        except RedisError as exc:
            logger.warning("Failed to refresh worker lock: %s", exc)
            return None

    def release(self) -> bool:
        """Release the lock only if this process still owns it."""
        try:
            redis_client = get_redis()
            result = redis_client.eval(_RELEASE_LUA, 1, self.key, self.token)
            return int(result or 0) == 1
        except RedisError as exc:
            logger.warning("Failed to release worker lock: %s", exc)
            return False

