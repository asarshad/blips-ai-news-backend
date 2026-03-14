from __future__ import annotations

from datetime import datetime, timedelta

from app.services.video_metrics_service import (
    _comparison_window,
    _inventory_state,
    _surface_floor,
    _surface_window_hours,
)


def test_surface_floors_match_launch_gates():
    assert _surface_floor("videos") == 60
    assert _surface_floor("reels") == 40


def test_inventory_state_flags_channel_dominance():
    assert (
        _inventory_state("videos", fresh_inventory=80, top_count=20, dominant_count=5)
        == "imbalanced"
    )
    assert (
        _inventory_state("reels", fresh_inventory=60, top_count=20, dominant_count=4)
        == "imbalanced"
    )
    assert (
        _inventory_state("videos", fresh_inventory=80, top_count=20, dominant_count=3) == "healthy"
    )


def test_surface_windows_follow_curated_weekly_model():
    assert _surface_window_hours("videos") == 168
    assert _surface_window_hours("reels") == 168


def test_comparison_window_preserves_same_window_size_when_shifted():
    now = datetime(2026, 3, 13, 12, 0, 0)

    start, end = _comparison_window(now=now, window_hours=168, lag=timedelta(hours=24))

    assert end == datetime(2026, 3, 12, 12, 0, 0)
    assert start == datetime(2026, 3, 5, 12, 0, 0)
