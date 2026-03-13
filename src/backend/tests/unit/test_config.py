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

    assert settings.YOUTUBE_CURATED_ONLY is True
    assert settings.YOUTUBE_DISCOVERY_ENABLED is False
    assert settings.YT_CURATED_LOOKBACK_HOURS == 168
