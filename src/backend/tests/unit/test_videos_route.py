from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi import Response

from app.api.routes import videos as videos_module


def test_get_recent_videos_surfaces_promoted_items_without_ai_gate(monkeypatch):
    captured = {}

    def _fake_feed(
        db,
        surface,
        *,
        limit,
        offset,
        require_ai_processed,
        hybrid_video_rerank,
        device_id=None,
    ):
        captured["surface"] = surface
        captured["limit"] = limit
        captured["offset"] = offset
        captured["require_ai_processed"] = require_ai_processed
        captured["hybrid_video_rerank"] = hybrid_video_rerank
        captured["device_id"] = device_id
        meta = SimpleNamespace(
            generated_at=datetime(2026, 3, 13, 12, 0, 0),
            source="db",
            cache_key="videos",
            cache_hit=False,
            tier_config={"fresh_hours": 168},
            remaining_window_count=0,
        )
        return (
            [
                {
                    "id": 1,
                    "title": "Fresh promoted video",
                    "published_at": "2026-03-13T11:55:00",
                    "freshness_tier": "A",
                }
            ],
            False,
            meta,
        )

    monkeypatch.setattr(videos_module, "get_cached_tiered_feed", _fake_feed)
    monkeypatch.setattr(videos_module, "check_and_trigger_topup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        videos_module.FeedMetadata,
        "add_headers",
        lambda self, response: response.headers.__setitem__("X-Test-Feed", "1"),
    )

    response = Response()
    result = videos_module.get_recent_videos(
        limit=1,
        cursor=None,
        page=None,
        x_device_id="device-12345678",
        response=response,
        db=object(),
        flags=SimpleNamespace(is_enabled=lambda name: name == "videos"),
    )

    assert captured["require_ai_processed"] is False
    assert captured["hybrid_video_rerank"] is False
    assert captured["device_id"] == "device-12345678"
    assert result["items"][0]["title"] == "Fresh promoted video"
    assert result["inventory_state"] == "caught_up"
    assert response.headers["X-Test-Feed"] == "1"


def test_get_recent_videos_passes_hybrid_rerank_flag(monkeypatch):
    captured = {}

    def _fake_feed(
        db,
        surface,
        *,
        limit,
        offset,
        require_ai_processed,
        hybrid_video_rerank,
        device_id=None,
    ):
        captured["surface"] = surface
        captured["hybrid_video_rerank"] = hybrid_video_rerank
        captured["device_id"] = device_id
        meta = SimpleNamespace(
            generated_at=datetime(2026, 3, 13, 12, 0, 0),
            source="db",
            cache_key="videos",
            cache_hit=False,
            tier_config={"fresh_hours": 168},
            remaining_window_count=0,
        )
        return ([], False, meta)

    monkeypatch.setattr(videos_module, "get_cached_tiered_feed", _fake_feed)
    monkeypatch.setattr(videos_module, "check_and_trigger_topup", lambda *_args, **_kwargs: None)

    result = videos_module.get_recent_videos(
        limit=1,
        cursor=None,
        page=None,
        x_device_id=None,
        response=Response(),
        db=object(),
        flags=SimpleNamespace(is_enabled=lambda name: name in {"videos", "video_hybrid_rerank"}),
    )

    assert captured["surface"].value == "videos"
    assert captured["hybrid_video_rerank"] is True
    assert captured["device_id"] is None
    assert result["inventory_state"] == "warming_up"


def test_get_recent_videos_uses_remaining_window_count_for_caught_up(monkeypatch):
    def _fake_feed(
        db,
        surface,
        *,
        limit,
        offset,
        require_ai_processed,
        hybrid_video_rerank,
        device_id=None,
    ):
        meta = SimpleNamespace(
            generated_at=datetime(2026, 3, 13, 12, 0, 0),
            source="db",
            cache_key="videos",
            cache_hit=False,
            tier_config={"fresh_hours": 168},
            remaining_window_count=0,
        )
        return (
            [
                {
                    "id": 1,
                    "title": "Page item",
                    "published_at": "2026-03-13T11:55:00",
                    "freshness_tier": "A",
                }
            ],
            True,
            meta,
        )

    monkeypatch.setattr(videos_module, "get_cached_tiered_feed", _fake_feed)
    monkeypatch.setattr(videos_module, "check_and_trigger_topup", lambda *_args, **_kwargs: None)

    result = videos_module.get_recent_videos(
        limit=1,
        cursor="5",
        page=None,
        x_device_id="device-abc",
        response=Response(),
        db=object(),
        flags=SimpleNamespace(is_enabled=lambda name: name == "videos"),
    )

    assert result["has_more"] is True
    assert result["remaining_count"] == 0
    assert result["inventory_state"] == "caught_up"
