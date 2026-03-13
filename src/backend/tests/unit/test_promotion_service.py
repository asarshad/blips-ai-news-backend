from __future__ import annotations

from app.services.promotion_service import (
    _REEL_CONFIG,
    _VIDEO_CONFIG,
    compute_story_keyword_signal,
)


def test_video_and_reel_configs_use_longer_half_lives():
    assert _VIDEO_CONFIG.recency_half_life_hours == 60.0
    assert _REEL_CONFIG.recency_half_life_hours == 30.0
    assert _VIDEO_CONFIG.w_story > 0.0
    assert _REEL_CONFIG.w_story > 0.0


def test_story_keyword_signal_recognizes_launch_framing():
    signal = compute_story_keyword_signal(
        "Apple launch keynote first look",
        "Hands on review with benchmark impressions.",
    )

    assert signal > 0.4
