from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi import Response

from app.api.routes import videos as videos_module


def test_get_recent_videos_surfaces_promoted_items_without_ai_gate(monkeypatch):
    captured = {}

    def _fake_feed(db, surface, *, limit, offset, require_ai_processed):
        captured["surface"] = surface
        captured["limit"] = limit
        captured["offset"] = offset
        captured["require_ai_processed"] = require_ai_processed
        meta = SimpleNamespace(
            generated_at=datetime(2026, 3, 13, 12, 0, 0),
            source="db",
            cache_key="videos",
            cache_hit=False,
            tier_config={"fresh_hours": 72},
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
        response=response,
        db=object(),
        flags=SimpleNamespace(is_enabled=lambda _name: True),
    )

    assert captured["require_ai_processed"] is False
    assert result["items"][0]["title"] == "Fresh promoted video"
    assert result["inventory_state"] == "caught_up"
    assert response.headers["X-Test-Feed"] == "1"
