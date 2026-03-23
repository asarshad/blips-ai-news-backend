"""Runtime push configuration service."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.push import PushClientConfig, PushMode, PushRuntimeConfig, PushRuntimeConfigPatch

logger = get_logger(__name__)

PUSH_CONFIG_REDIS_KEY = "blips:push:config"


class PushConfigService:
    """Read and mutate runtime push configuration."""

    def __init__(self, redis_client: Optional[Redis] = None) -> None:
        self._redis = redis_client

    def get_default_config(self) -> PushRuntimeConfig:
        """Return env-backed defaults for runtime push."""
        return PushRuntimeConfig(
            enabled=settings.PUSH_RUNTIME_ENABLED,
            mode=PushMode(settings.PUSH_MODE),
            config_ttl_seconds=settings.PUSH_CONFIG_TTL_SECONDS,
        )

    def get_raw_config(self) -> tuple[PushRuntimeConfig, str]:
        """Return stored push config or environment defaults."""
        default = self.get_default_config()
        if self._redis is None:
            return default, "default"

        try:
            payload = self._redis.get(PUSH_CONFIG_REDIS_KEY)
        except RedisError as exc:
            logger.warning("Failed to read push config from Redis: %s", exc)
            return default, "default"

        if not payload:
            return default, "default"

        try:
            return PushRuntimeConfig.model_validate_json(payload), "redis"
        except ValidationError as exc:
            logger.warning("Invalid push config in Redis, falling back to defaults: %s", exc)
            return default, "default"

    def get_public_config(self, *, provider_ready: bool) -> PushClientConfig:
        """Return the effective client-facing push config."""
        raw, _source = self.get_raw_config()
        enabled = raw.enabled and raw.mode != PushMode.disabled and provider_ready
        return PushClientConfig(
            enabled=enabled,
            mode=raw.mode,
            config_ttl_seconds=raw.config_ttl_seconds,
        )

    def update_config(self, patch: PushRuntimeConfigPatch) -> PushRuntimeConfig:
        """Persist a patched config to Redis."""
        current, _source = self.get_raw_config()
        merged = current.model_dump()
        self._deep_merge(merged, patch.model_dump(exclude_none=True))
        updated = PushRuntimeConfig.model_validate(merged)
        self._store_config(updated)
        return updated

    def reset_config(self) -> PushRuntimeConfig:
        """Clear Redis override and return env defaults."""
        redis_client = self._require_redis()
        try:
            redis_client.delete(PUSH_CONFIG_REDIS_KEY)
        except RedisError as exc:
            raise RuntimeError("Failed to clear push config override") from exc
        return self.get_default_config()

    def _store_config(self, config: PushRuntimeConfig) -> None:
        redis_client = self._require_redis()
        try:
            redis_client.set(PUSH_CONFIG_REDIS_KEY, config.model_dump_json())
        except RedisError as exc:
            raise RuntimeError("Failed to persist push config override") from exc

    def _require_redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Redis unavailable")
        return self._redis

    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                PushConfigService._deep_merge(base[key], value)
            else:
                base[key] = value
