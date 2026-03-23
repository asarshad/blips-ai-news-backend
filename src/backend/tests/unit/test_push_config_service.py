from __future__ import annotations

import fakeredis

from app.core.config import settings
from app.schemas.push import PushRuntimeConfigPatch
from app.services.push_config_service import PUSH_CONFIG_REDIS_KEY, PushConfigService


def test_public_config_disables_push_when_provider_unavailable(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    service = PushConfigService(redis_client=redis_client)
    monkeypatch.setattr(settings, "PUSH_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "PUSH_MODE", "auto_all")
    monkeypatch.setattr(settings, "PUSH_CONFIG_TTL_SECONDS", 180)

    config = service.get_public_config(provider_ready=False)

    assert config.enabled is False
    assert config.mode.value == "auto_all"
    assert config.config_ttl_seconds == 180


def test_update_config_persists_partial_patch_without_resetting_other_fields(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    service = PushConfigService(redis_client=redis_client)
    monkeypatch.setattr(settings, "PUSH_RUNTIME_ENABLED", False)
    monkeypatch.setattr(settings, "PUSH_MODE", "manual")
    monkeypatch.setattr(settings, "PUSH_CONFIG_TTL_SECONDS", 300)

    updated = service.update_config(
        PushRuntimeConfigPatch.model_validate(
            {"enabled": True, "mode": "auto_all"},
        ),
    )

    assert updated.enabled is True
    assert updated.mode.value == "auto_all"
    assert updated.config_ttl_seconds == 300
    assert redis_client.get(PUSH_CONFIG_REDIS_KEY) is not None


def test_reset_config_clears_redis_override_and_returns_defaults(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    service = PushConfigService(redis_client=redis_client)
    monkeypatch.setattr(settings, "PUSH_RUNTIME_ENABLED", False)
    monkeypatch.setattr(settings, "PUSH_MODE", "manual")
    monkeypatch.setattr(settings, "PUSH_CONFIG_TTL_SECONDS", 300)
    service.update_config(PushRuntimeConfigPatch(enabled=True, mode="auto_all"))

    reset = service.reset_config()

    assert redis_client.get(PUSH_CONFIG_REDIS_KEY) is None
    assert reset.enabled is False
    assert reset.mode.value == "manual"
    assert reset.config_ttl_seconds == 300
