from __future__ import annotations

from app.services.video_metrics_service import _inventory_state, _surface_floor


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
