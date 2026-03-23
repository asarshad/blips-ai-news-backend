from __future__ import annotations

from app.core.config import Settings


def test_video_and_reel_targets_default_higher_for_feed_density():
    settings = Settings()

    assert settings.DAILY_TARGET_ARTICLES == 100
    assert settings.DAILY_TARGET_VIDEOS == 60
    assert settings.DAILY_TARGET_REELS == 40


def test_inventory_threshold_defaults_match_higher_targets():
    settings = Settings()

    assert settings.MIN_FRESH_ARTICLES == 50
    assert settings.MIN_FRESH_VIDEOS == 60
    assert settings.MIN_FRESH_REELS == 40
    assert settings.RESERVOIR_ARTICLES == 250
    assert settings.RESERVOIR_VIDEOS == 180
    assert settings.RESERVOIR_REELS == 320


def test_curated_only_youtube_defaults():
    settings = Settings()

    assert settings.YOUTUBE_CURATED_ONLY is False
    assert settings.YOUTUBE_DISCOVERY_ENABLED is True
    assert settings.YT_CURATED_LOOKBACK_HOURS == 168


def test_runtime_ads_defaults_support_backend_controlled_surfaces():
    settings = Settings()

    assert settings.ADS_RUNTIME_ENABLED is True
    assert settings.ADS_PROVIDER == "admob_native"
    assert settings.ADS_RUNTIME_CANARY_PERCENT == 5
    assert settings.ADS_CONFIG_TTL_SECONDS == 300

    assert settings.ADS_ARTICLES_ENABLED is True
    assert settings.ADS_ARTICLES_FREQUENCY == 8
    assert settings.ADS_ARTICLES_FIRST_SLOT_AFTER == 2

    assert settings.ADS_VIDEOS_ENABLED is True
    assert settings.ADS_VIDEOS_FREQUENCY == 8
    assert settings.ADS_VIDEOS_FIRST_SLOT_AFTER == 2

    assert settings.ADS_REELS_ENABLED is False
    assert settings.ADS_REELS_FREQUENCY == 0
    assert settings.ADS_REELS_FIRST_SLOT_AFTER == 0


def test_runtime_push_defaults_support_backend_control():
    settings = Settings()

    assert settings.PUSH_RUNTIME_ENABLED is False
    assert settings.PUSH_MODE == "manual"
    assert settings.PUSH_CONFIG_TTL_SECONDS == 300
    assert settings.FIREBASE_SERVICE_ACCOUNT_JSON == ""
    assert settings.FIREBASE_SERVICE_ACCOUNT_FILE == ""
