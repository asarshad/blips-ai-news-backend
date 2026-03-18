from app.api.admin import ui as admin_ui
from app.services import video_metrics_service


def test_ui_video_lanes_query_view_renders_query_labels(monkeypatch):
    def _fake_metrics(db, hours=24, breakdown=None):
        assert breakdown == "query"
        return {
            "surfaces": {
                "videos": [
                    {
                        "query_label": "ai-models",
                        "candidates": 20,
                        "promoted": 4,
                        "promotion_rate": 20.0,
                        "median_promoted_age": 2.5,
                        "distinct_promoted_channels": 3,
                        "duplicate_rejection_rate": 5.0,
                        "clickbait_rejection_rate": 10.0,
                        "filtered_non_english": 1,
                        "filtered_live": 1,
                        "filtered_off_topic": 2,
                        "filtered_format": 0,
                    }
                ],
                "reels": [],
            }
        }

    monkeypatch.setattr(video_metrics_service, "compute_video_lane_metrics", _fake_metrics)

    response = admin_ui.ui_video_lanes(hours=24, view="query", db=object(), admin_key="test")
    html = response.body.decode("utf-8")

    assert "ai-models" in html
    assert "Active querys" not in html
    assert "Active queries" in html


def test_ui_video_lanes_query_view_formats_missing_rates(monkeypatch):
    def _fake_metrics(db, hours=24, breakdown=None):
        assert breakdown == "query"
        return {
            "surfaces": {
                "videos": [
                    {
                        "query_label": "search",
                        "candidates": 0,
                        "promoted": 1,
                        "promotion_rate": None,
                        "median_promoted_age": 2.5,
                        "distinct_promoted_channels": 1,
                        "duplicate_rejection_rate": 0.0,
                        "clickbait_rejection_rate": 0.0,
                        "filtered_non_english": 0,
                        "filtered_live": 0,
                        "filtered_off_topic": 0,
                        "filtered_format": 0,
                    }
                ],
                "reels": [],
            }
        }

    monkeypatch.setattr(video_metrics_service, "compute_video_lane_metrics", _fake_metrics)

    response = admin_ui.ui_video_lanes(hours=24, view="query", db=object(), admin_key="test")
    html = response.body.decode("utf-8")

    assert "None%" not in html
