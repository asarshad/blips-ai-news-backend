from __future__ import annotations

import fakeredis

from app.schemas.ads import AdsRuntimeConfigPatch
from app.services.ad_config_service import ADS_CONFIG_REDIS_KEY, AdConfigService


def test_public_config_disables_ads_without_device_id():
    service = AdConfigService(redis_client=fakeredis.FakeRedis())

    config = service.get_public_config(None)

    assert config.enabled is False
    assert config.eligible is False
    assert config.surfaces.articles.enabled is False
    assert config.surfaces.videos.enabled is False
    assert config.surfaces.reels.enabled is False


def test_public_config_eligibility_is_stable_for_same_device():
    service = AdConfigService(redis_client=fakeredis.FakeRedis())
    device_id = "device-12345678"

    first = service.get_public_config(device_id)
    second = service.get_public_config(device_id)

    assert first.eligible == second.eligible
    assert first.surfaces.articles.enabled == second.surfaces.articles.enabled


def test_update_config_patches_nested_surface_fields_without_overwriting_others():
    redis_client = fakeredis.FakeRedis()
    service = AdConfigService(redis_client=redis_client)

    updated = service.update_config(
        AdsRuntimeConfigPatch.model_validate(
            {
                "surfaces": {
                    "articles": {
                        "enabled": False,
                        "frequency": 5,
                    },
                },
            },
        ),
    )

    assert updated.surfaces.articles.enabled is False
    assert updated.surfaces.articles.frequency == 5
    assert updated.surfaces.videos.enabled is True
    assert updated.surfaces.videos.frequency == 8
    assert redis_client.get(ADS_CONFIG_REDIS_KEY) is not None


def test_reset_config_clears_redis_override_and_uses_defaults():
    redis_client = fakeredis.FakeRedis()
    service = AdConfigService(redis_client=redis_client)
    service.update_config(AdsRuntimeConfigPatch(enabled=False))

    reset = service.reset_config()

    assert redis_client.get(ADS_CONFIG_REDIS_KEY) is None
    assert reset.enabled is True
    assert reset.surfaces.reels.enabled is False
