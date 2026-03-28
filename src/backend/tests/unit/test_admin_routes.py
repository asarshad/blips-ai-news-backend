from __future__ import annotations

from types import SimpleNamespace

import fakeredis

from app.api.admin import routes as admin_routes
from app.api.routes import admin as admin_module
from app.schemas.ads import AdsRuntimeConfigPatch
from app.schemas.push import PushRuntimeConfigPatch, PushSendResponse
from app.services.push_service import PushNotificationError


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


class _FakeEditorialRepo:
    last_list_args = None

    def __init__(self, db):
        self.db = db

    def list_content(self, **kwargs):
        _FakeEditorialRepo.last_list_args = kwargs
        return [], 0


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


def test_get_push_config_reports_default_source_and_provider_state(monkeypatch):
    service = admin_module.PushConfigService(redis_client=fakeredis.FakeRedis())
    monkeypatch.setattr(
        admin_module,
        "create_push_messaging_client",
        lambda: SimpleNamespace(is_available=False),
    )

    result = admin_module.get_push_config(service)

    assert result.source == "default"
    assert result.push.enabled is False
    assert result.push.mode.value == "manual"
    assert result.provider_ready is False


def test_patch_push_config_updates_mode_and_enabled(monkeypatch):
    service = admin_module.PushConfigService(redis_client=fakeredis.FakeRedis())
    monkeypatch.setattr(
        admin_module,
        "create_push_messaging_client",
        lambda: SimpleNamespace(is_available=True),
    )

    result = admin_module.patch_push_config(
        PushRuntimeConfigPatch(enabled=True, mode="auto_all"),
        service,
    )

    assert result.source == "redis"
    assert result.push.enabled is True
    assert result.push.mode.value == "auto_all"
    assert result.provider_ready is True


def test_reset_push_config_returns_default_source(monkeypatch):
    service = admin_module.PushConfigService(redis_client=fakeredis.FakeRedis())
    monkeypatch.setattr(
        admin_module,
        "create_push_messaging_client",
        lambda: SimpleNamespace(is_available=False),
    )
    admin_module.patch_push_config(
        PushRuntimeConfigPatch(enabled=True, mode="auto_all"),
        service,
    )

    result = admin_module.reset_push_config(service)

    assert result.source == "default"
    assert result.push.enabled is False
    assert result.push.mode.value == "manual"
    assert result.provider_ready is False


def test_send_push_now_returns_service_result(monkeypatch):
    expected = PushSendResponse(
        success=True,
        skipped=False,
        content_id=42,
        mode="manual",
        audience_count=3,
        success_count=3,
        failure_count=0,
        invalid_token_count=0,
        log_id=7,
        message="Push notification sent.",
    )

    class _FakePushService:
        def send_manual(self, *, content_id: int, actor: str):
            assert content_id == 42
            assert actor == "admin"
            return expected

    monkeypatch.setattr(admin_module, "PushNotificationService", lambda db: _FakePushService())

    result = admin_module.send_push_now(content_id=42, db=object())

    assert result == expected


def test_send_push_now_maps_service_error_to_http_400(monkeypatch):
    class _FakePushService:
        def send_manual(self, *, content_id: int, actor: str):
            raise PushNotificationError("Push notifications are disabled")

    monkeypatch.setattr(admin_module, "PushNotificationService", lambda db: _FakePushService())

    try:
        admin_module.send_push_now(content_id=11, db=object())
    except Exception as exc:  # pragma: no cover - explicit assertions below
        assert getattr(exc, "status_code", None) == 400
        assert "Push notifications are disabled" in str(getattr(exc, "detail", ""))
    else:  # pragma: no cover
        raise AssertionError("Expected HTTPException for push send failure")


def test_trigger_image_recovery_eval_returns_service_result(monkeypatch):
    expected = {"scanned": 12, "recovered": 5, "success_rate_percent": 41.7}

    class _FakeSession:
        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr("app.db.base.SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.services.article_image_service.evaluate_llm_article_image_recovery",
        lambda db, **kwargs: expected,
    )

    result = admin_module.trigger_image_recovery_eval(
        admin_module.ArticleImageRecoveryEvalRequest(
            lookback_days=7,
            limit=100,
            sample_size=10,
            apply=False,
        )
    )

    assert result == {"status": "ok", "result": expected}


def test_list_content_passes_has_image_filter(monkeypatch):
    _FakeEditorialRepo.last_list_args = None
    monkeypatch.setattr(admin_routes, "EditorialRepository", _FakeEditorialRepo)

    response = admin_routes.list_content(
        day=None,
        type="ARTICLE",
        source="example.com",
        suppressed=False,
        manual_added=None,
        has_image=False,
        sort_by="published_at",
        page=1,
        page_size=50,
        db=object(),
    )

    assert _FakeEditorialRepo.last_list_args is not None
    assert _FakeEditorialRepo.last_list_args["content_type"] == "ARTICLE"
    assert _FakeEditorialRepo.last_list_args["source"] == "example.com"
    assert _FakeEditorialRepo.last_list_args["suppressed"] is False
    assert _FakeEditorialRepo.last_list_args["has_image"] is False
    assert response.total == 0
