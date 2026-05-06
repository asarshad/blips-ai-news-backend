from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api.routes import config as config_module
from app.core.session_auth import AuthenticatedSession
from app.schemas.ads import AdsClientConfig, AppConfigResponse
from app.schemas.push import PushClientConfig


def _session(device_id: str = "device-abc123") -> AuthenticatedSession:
    import datetime

    return AuthenticatedSession(
        device_id=device_id,
        platform="ios",
        app_version="1.0.3+15",
        session_expires_at=datetime.datetime.now(datetime.timezone.utc),
    )


class _FakeAdConfigService:
    def get_public_config(self, device_id: str) -> AdsClientConfig:
        return AdsClientConfig()


class _FakePushConfigService:
    def get_public_config(self, *, provider_ready: bool) -> PushClientConfig:
        return PushClientConfig()


_fake_push_client = SimpleNamespace(is_available=False)


def _call_get_app_config(
    *,
    min_recommended_version: str = "",
    ios_app_store_url: str = "",
    android_play_store_url: str = "",
) -> AppConfigResponse:
    with (
        patch.object(config_module, "settings") as mock_settings,
        patch.object(
            config_module,
            "create_push_messaging_client",
            return_value=_fake_push_client,
        ),
    ):
        mock_settings.MIN_RECOMMENDED_VERSION = min_recommended_version
        mock_settings.IOS_APP_STORE_URL = ios_app_store_url
        mock_settings.ANDROID_PLAY_STORE_URL = android_play_store_url

        return config_module.get_app_config(
            session=_session(),
            ad_config_service=_FakeAdConfigService(),
            push_config_service=_FakePushConfigService(),
        )


def test_config_returns_empty_version_when_nudge_not_configured():
    response = _call_get_app_config()

    assert response.min_recommended_version == ""
    assert response.ios_app_store_url == ""
    assert response.android_play_store_url == ""


def test_config_returns_min_recommended_version_from_settings():
    response = _call_get_app_config(min_recommended_version="1.0.5")

    assert response.min_recommended_version == "1.0.5"


def test_config_returns_store_urls_from_settings():
    response = _call_get_app_config(
        min_recommended_version="1.0.5",
        ios_app_store_url="https://apps.apple.com/app/id1234567890",
        android_play_store_url="https://play.google.com/store/apps/details?id=com.blips.blips_mobile",
    )

    assert response.ios_app_store_url == "https://apps.apple.com/app/id1234567890"
    assert response.android_play_store_url == (
        "https://play.google.com/store/apps/details?id=com.blips.blips_mobile"
    )


def test_config_still_returns_ads_and_push_alongside_version():
    response = _call_get_app_config(min_recommended_version="1.0.5")

    assert isinstance(response.ads, AdsClientConfig)
    assert isinstance(response.push, PushClientConfig)
