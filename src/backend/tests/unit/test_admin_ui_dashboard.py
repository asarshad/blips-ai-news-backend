from __future__ import annotations

from types import SimpleNamespace

from app.api.admin import ui as admin_ui
from app.core import feature_flags as feature_flags_module
from app.services import inventory_service, tiered_feed_service, video_metrics_service


class _FakeQuery:
    def __init__(self, *, all_result=None, scalar_result=None):
        self._all_result = [] if all_result is None else all_result
        self._scalar_result = scalar_result

    def filter(self, *_args, **_kwargs):
        return self

    def group_by(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._all_result)

    def scalar(self):
        return self._scalar_result


class _FakeDB:
    def __init__(self, queries: list[_FakeQuery]):
        self._queries = list(queries)

    def query(self, *_args, **_kwargs):
        assert self._queries, "Unexpected query"
        return self._queries.pop(0)


def _surface_payload(*, fresh_inventory_window: int) -> dict:
    return {
        "fresh_inventory_window": fresh_inventory_window,
        "deltas": {
            "fresh_inventory_24h": {"baseline": 0.0, "vs_24h": 0.0, "vs_7d": 0.0},
            "dominant_channel_pct_top20": {"baseline": 0.0, "vs_24h": 0.0, "vs_7d": 0.0},
        },
        "inventory_window_label": "7d",
        "inventory_state": "healthy",
        "issues": [],
        "recent_refresh_count": 12,
        "recent_refresh_threshold": 8,
        "refresh_window_hours": 24,
        "median_age_top20_hours": 3.5,
        "distinct_active_channels_window": 9,
        "dominant_channel_pct_top20": 15.0,
        "is_healthy": True,
        "reservoir_count": 100,
        "reservoir_threshold": 50,
    }


def test_ui_dashboard_renders_when_lane_promotion_rate_is_missing(monkeypatch):
    class _FakeFlags:
        def is_enabled(self, feature: str) -> bool:
            assert feature == "video_hybrid_rerank"
            return False

    monkeypatch.setattr(feature_flags_module, "FeatureFlags", _FakeFlags)
    monkeypatch.setattr(
        video_metrics_service,
        "compute_video_supply_metrics",
        lambda db: {
            "baseline_tag": "test-baseline",
            "surfaces": {
                "videos": _surface_payload(fresh_inventory_window=120),
                "reels": _surface_payload(fresh_inventory_window=80),
            },
        },
    )
    monkeypatch.setattr(
        video_metrics_service,
        "compute_video_lane_metrics",
        lambda db, hours=24: {
            "surfaces": {
                "videos": [
                    {
                        "lane": "search",
                        "candidates": 0,
                        "promoted": 1,
                        "promotion_rate": None,
                        "median_promoted_age": 2.5,
                        "distinct_promoted_channels": 1,
                        "duplicate_rejection_rate": 0.0,
                        "clickbait_rejection_rate": 0.0,
                    },
                    {
                        "lane": "trending",
                        "candidates": 10,
                        "promoted": 1,
                        "promotion_rate": 10.0,
                        "median_promoted_age": 1.5,
                        "distinct_promoted_channels": 2,
                        "duplicate_rejection_rate": 5.0,
                        "clickbait_rejection_rate": 0.0,
                    },
                ],
                "reels": [],
            }
        },
    )
    monkeypatch.setattr(
        inventory_service,
        "get_pipeline_counts",
        lambda db: {"window_hours": 48, "per_type": {}},
    )

    meta = SimpleNamespace(remaining_window_count=0)
    monkeypatch.setattr(
        tiered_feed_service,
        "get_cached_tiered_feed",
        lambda *args, **kwargs: ([], False, meta),
    )

    fake_db = _FakeDB(
        [
            _FakeQuery(all_result=[]),  # day_rows
            _FakeQuery(scalar_result=None),  # avg_score
            _FakeQuery(scalar_result=0),  # signal_seen
            _FakeQuery(all_result=[]),  # signal_by_status
            _FakeQuery(all_result=[]),  # source_rows
            _FakeQuery(all_result=[]),  # pending_promo
            _FakeQuery(all_result=[]),  # mix_rows
            _FakeQuery(all_result=[]),  # source_status_counts
            _FakeQuery(all_result=[]),  # recent_source_actions
            _FakeQuery(scalar_result=0),  # reel_total_count
            _FakeQuery(scalar_result=0),  # reel_known_count
            _FakeQuery(scalar_result=0),  # reel_duration_violations
            _FakeQuery(scalar_result=None),  # reel_max_duration
        ]
    )

    response = admin_ui.ui_dashboard(day=None, db=fake_db, admin_key="test")
    html = response.body.decode("utf-8")

    assert "Lane snapshot (24h)" in html
    assert "Videos · trending" in html
    assert "Videos · search" in html
