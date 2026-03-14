"""Runtime ad configuration service.

Stores a single ads configuration blob in Redis with environment defaults as a
fallback. Public config is caller-specific: device-less requests fail closed,
and canary eligibility is derived from the caller's device identifier.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.ads import (
    AdsClientConfig,
    AdsRuntimeConfig,
    AdsRuntimeConfigPatch,
    AdsSurfacesConfig,
    AdSurfaceConfig,
)

logger = get_logger(__name__)

ADS_CONFIG_REDIS_KEY = "blips:ads:config"


class AdConfigService:
    """Read and mutate runtime ad configuration."""

    def __init__(
        self,
        redis_client: Optional[Redis] = None,
    ) -> None:
        self._redis = redis_client

    def get_default_config(self) -> AdsRuntimeConfig:
        """Return env-backed defaults for runtime mobile ads."""
        return AdsRuntimeConfig(
            enabled=settings.ADS_RUNTIME_ENABLED,
            provider=settings.ADS_PROVIDER,
            canary_percent=settings.ADS_RUNTIME_CANARY_PERCENT,
            config_ttl_seconds=settings.ADS_CONFIG_TTL_SECONDS,
            surfaces=AdsSurfacesConfig(
                articles=AdSurfaceConfig(
                    enabled=settings.ADS_ARTICLES_ENABLED,
                    frequency=settings.ADS_ARTICLES_FREQUENCY,
                    first_slot_after=settings.ADS_ARTICLES_FIRST_SLOT_AFTER,
                ),
                videos=AdSurfaceConfig(
                    enabled=settings.ADS_VIDEOS_ENABLED,
                    frequency=settings.ADS_VIDEOS_FREQUENCY,
                    first_slot_after=settings.ADS_VIDEOS_FIRST_SLOT_AFTER,
                ),
                reels=AdSurfaceConfig(
                    enabled=settings.ADS_REELS_ENABLED,
                    frequency=settings.ADS_REELS_FREQUENCY,
                    first_slot_after=settings.ADS_REELS_FIRST_SLOT_AFTER,
                ),
            ),
        )

    def get_raw_config(self) -> tuple[AdsRuntimeConfig, str]:
        """Return stored config or environment defaults."""
        default = self.get_default_config()
        if self._redis is None:
            return default, "default"

        try:
            payload = self._redis.get(ADS_CONFIG_REDIS_KEY)
        except RedisError as exc:
            logger.warning("Failed to read ads config from Redis: %s", exc)
            return default, "default"

        if not payload:
            return default, "default"

        try:
            return AdsRuntimeConfig.model_validate_json(payload), "redis"
        except ValidationError as exc:
            logger.warning("Invalid ads config in Redis, falling back to defaults: %s", exc)
            return default, "default"

    def get_public_config(self, device_id: Optional[str]) -> AdsClientConfig:
        """Return the effective config for a caller."""
        raw, _source = self.get_raw_config()
        normalized_device_id = (device_id or "").strip()
        enabled = raw.enabled and bool(normalized_device_id)
        eligible = enabled and self._is_canary_eligible(
            device_id=normalized_device_id,
            canary_percent=raw.canary_percent,
        )

        def effective_surface(surface: AdSurfaceConfig) -> AdSurfaceConfig:
            return surface.model_copy(
                update={
                    "enabled": surface.enabled and enabled and eligible,
                },
            )

        return AdsClientConfig(
            enabled=enabled,
            provider=raw.provider,
            eligible=eligible,
            canary_percent=raw.canary_percent,
            config_ttl_seconds=raw.config_ttl_seconds,
            surfaces=AdsSurfacesConfig(
                articles=effective_surface(raw.surfaces.articles),
                videos=effective_surface(raw.surfaces.videos),
                reels=effective_surface(raw.surfaces.reels),
            ),
        )

    def update_config(self, patch: AdsRuntimeConfigPatch) -> AdsRuntimeConfig:
        """Persist a patched config to Redis."""
        current, _source = self.get_raw_config()
        merged = current.model_dump()
        self._deep_merge(merged, patch.model_dump(exclude_none=True))
        updated = AdsRuntimeConfig.model_validate(merged)
        self._store_config(updated)
        return updated

    def reset_config(self) -> AdsRuntimeConfig:
        """Clear Redis override and return env defaults."""
        redis_client = self._require_redis()
        try:
            redis_client.delete(ADS_CONFIG_REDIS_KEY)
        except RedisError as exc:
            raise RuntimeError("Failed to clear ads config override") from exc
        return self.get_default_config()

    def _store_config(self, config: AdsRuntimeConfig) -> None:
        redis_client = self._require_redis()
        try:
            redis_client.set(ADS_CONFIG_REDIS_KEY, config.model_dump_json())
        except RedisError as exc:
            raise RuntimeError("Failed to persist ads config override") from exc

    def _require_redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Redis unavailable")
        return self._redis

    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                AdConfigService._deep_merge(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def _is_canary_eligible(device_id: str, canary_percent: int) -> bool:
        if canary_percent <= 0 or canary_percent >= 100:
            return True
        digest = hashlib.md5(device_id.encode("utf-8")).hexdigest()  # noqa: S324
        bucket = int(digest[:8], 16) % 100
        return bucket < canary_percent
