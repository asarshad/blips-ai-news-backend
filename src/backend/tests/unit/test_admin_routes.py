from __future__ import annotations

import fakeredis

from app.api.routes import admin as admin_module
from app.schemas.ads import AdsRuntimeConfigPatch


class _FakeRedis:
    def __init__(self):
        self.deleted = []

    def delete(self, *keys):
        self.deleted.extend(keys)
        return len(keys)

    def get(self, _key):
        return None

    def exists(self, _key):
        return 0


def test_reset_youtube_search_cooldown_clears_both_surfaces(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.core.dependencies.get_redis", lambda: fake_redis)

    result = admin_module.reset_youtube_search_cooldown()

    assert result["status"] == "ok"
    assert result["surfaces"] == ["videos", "reels"]
    assert result["deleted_keys"] == 2
    assert fake_redis.deleted == [
        "blips:youtube:search:cooldown:videos",
        "blips:youtube:search:cooldown:reels",
    ]


def test_reset_youtube_search_cooldown_validates_surface(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.core.dependencies.get_redis", lambda: fake_redis)

    try:
        admin_module.reset_youtube_search_cooldown("articles")
    except Exception as exc:  # pragma: no cover - explicit assertion below
        assert getattr(exc, "status_code", None) == 400
        assert "surface must be" in str(getattr(exc, "detail", ""))
    else:  # pragma: no cover
        raise AssertionError("Expected HTTPException for invalid surface")


def test_get_ads_config_reports_default_source():
    service = admin_module.AdConfigService(redis_client=fakeredis.FakeRedis())

    result = admin_module.get_ads_config(service)

    assert result.source == "default"
    assert result.ads.enabled is True
    assert result.ads.surfaces.reels.enabled is False


def test_patch_ads_config_updates_selected_surface_only():
    service = admin_module.AdConfigService(redis_client=fakeredis.FakeRedis())

    result = admin_module.patch_ads_config(
        AdsRuntimeConfigPatch.model_validate(
            {
                "surfaces": {
                    "reels": {
                        "enabled": True,
                        "frequency": 4,
                        "first_slot_after": 3,
                    },
                },
            },
        ),
        service,
    )

    assert result.source == "redis"
    assert result.ads.surfaces.reels.enabled is True
    assert result.ads.surfaces.reels.frequency == 4
    assert result.ads.surfaces.articles.frequency == 8


def test_reset_ads_config_returns_default_source():
    service = admin_module.AdConfigService(redis_client=fakeredis.FakeRedis())
    admin_module.patch_ads_config(AdsRuntimeConfigPatch(enabled=False), service)

    result = admin_module.reset_ads_config(service)

    assert result.source == "default"
    assert result.ads.enabled is True
