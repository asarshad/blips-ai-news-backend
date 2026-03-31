from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.content import ContentType
from app.services import tiered_feed_service
from app.services.inventory_service import FreshnessTier, Surface
from app.services.tiered_feed_service import (
    TieredItem,
    _cache_key,
    _prioritize_unseen_items,
    tiered_item_to_dict,
)


def test_tiered_item_to_dict_falls_back_to_video_description_when_summary_missing():
    item = SimpleNamespace(
        id=42,
        type=ContentType.VIDEO,
        title="Fresh video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        summary=None,
        description="This is a detailed video description that should be used immediately while AI enrichment catches up.",
        image_url="https://img.youtube.com/vi/test123/maxresdefault.jpg",
        source="Trusted Source",
        created_at=datetime(2026, 3, 13, 12, 0, 0),
        published_at=datetime(2026, 3, 13, 11, 55, 0),
        global_score=0.44,
        promotion_score=0.37,
        recency_score=0.95,
        topics=["Technology"],
        channel_id="channel-1",
        acquisition_lane="search",
        source_status="discovery",
        views_per_hour=120.0,
        format_fit_score=1.0,
        promotion_reason="search|discovery",
        conversation_starters=None,
        duration_seconds=420,
    )

    tiered = TieredItem(
        item=item,
        tier=FreshnessTier.A,
        reason="fresh_published",
        published_age_seconds=300,
        added_age_seconds=60,
    )

    result = tiered_item_to_dict(tiered)

    assert result["summary"].startswith("This is a detailed video description")
    assert result["video_url"] == "https://www.youtube.com/watch?v=test123"
    assert result["conversation_starters"]["fallback"]


def test_cache_key_separates_hybrid_video_rerank_variants():
    base_key = _cache_key(
        surface=SimpleNamespace(value="videos"),
        limit=20,
        offset=0,
        hybrid_video_rerank=False,
    )
    hybrid_key = _cache_key(
        surface=SimpleNamespace(value="videos"),
        limit=20,
        offset=0,
        hybrid_video_rerank=True,
    )

    assert base_key != hybrid_key
    assert base_key.endswith("hybrid0")
    assert hybrid_key.endswith("hybrid1")


def test_cache_key_includes_device_hash_when_personalized():
    personalized_key = _cache_key(
        surface=SimpleNamespace(value="videos"),
        limit=20,
        offset=0,
        hybrid_video_rerank=False,
        device_id="device-12345678",
    )

    assert personalized_key.startswith("blips:tiered_feed:videos:")
    assert ":d" in personalized_key


def test_prioritize_unseen_items_only_uses_demoted_fill_when_needed():
    primary = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
    demoted = [SimpleNamespace(id=4), SimpleNamespace(id=5)]

    selected = _prioritize_unseen_items(primary, demoted, target_size=3)
    assert [item.id for item in selected] == [1, 2, 3]

    selected = _prioritize_unseen_items(primary, demoted, target_size=4)
    assert [item.id for item in selected] == [1, 2, 3, 4]


def test_get_tiered_feed_applies_hybrid_rerank_to_reels_when_enabled(monkeypatch):
    now = datetime(2026, 3, 20, 12, 0, 0)
    items = [
        SimpleNamespace(
            id=index,
            source=f"Source {index}",
            channel_id=f"channel-{index}",
            published_at=now - timedelta(hours=index),
            created_at=now - timedelta(hours=index),
        )
        for index in range(1, 10)
    ]

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def count(self):
            return len(items)

        def all(self):
            return list(items)

    class _FakeDB:
        def query(self, *_args, **_kwargs):
            return _FakeQuery()

    rerank_calls = []

    monkeypatch.setattr(tiered_feed_service, "apply_content_policy", lambda query, **_kwargs: query)
    monkeypatch.setattr(
        tiered_feed_service,
        "_get_surface_config",
        lambda _surface: {"fresh_hours": 168, "backfill_hours": 336, "evergreen_days": 30},
    )
    monkeypatch.setattr(tiered_feed_service, "make_default_policy", lambda **_kwargs: None)
    monkeypatch.setattr(
        tiered_feed_service,
        "build_surface_age_filters",
        lambda **_kwargs: SimpleNamespace(fresh=True, backfill=True, evergreen_tier=True),
    )
    monkeypatch.setattr(
        tiered_feed_service, "_get_recent_feedback_ids", lambda *_args: (set(), set())
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "rerank_video_candidates",
        lambda feed_items, *, target_count, surface="videos": (
            rerank_calls.append(
                {
                    "surface": surface,
                    "target_count": target_count,
                    "item_ids": [item.id for item in feed_items],
                }
            )
            or list(reversed(feed_items))
        ),
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "mix_feed",
        lambda feed_items, surface, target_size, session_seed=None: list(feed_items)[:target_size],
    )
    monkeypatch.setattr(
        tiered_feed_service, "enforce_channel_caps", lambda feed_items, surface: feed_items
    )

    tiered_items, has_more, remaining = tiered_feed_service.get_tiered_feed(
        _FakeDB(),
        Surface.REELS,
        limit=2,
        offset=0,
        now=now,
        hybrid_video_rerank=True,
    )

    assert rerank_calls == [
        {
            "surface": "reels",
            "target_count": 3,
            "item_ids": [item.id for item in items],
        }
    ]
    assert [tiered.item.id for tiered in tiered_items] == [9, 8]
    assert has_more is True
    assert remaining == 7


def test_get_tiered_feed_filters_recent_negative_feedback(monkeypatch):
    now = datetime(2026, 3, 20, 12, 0, 0)
    items = [
        SimpleNamespace(
            id=1,
            source="Source 1",
            channel_id="creator-1",
            published_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=1),
        ),
        SimpleNamespace(
            id=2,
            source="Source 2",
            channel_id="creator-2",
            published_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=2),
        ),
        SimpleNamespace(
            id=3,
            source="Source 3",
            channel_id="creator-3",
            published_at=now - timedelta(hours=3),
            created_at=now - timedelta(hours=3),
        ),
    ]

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def count(self):
            return len(items)

        def all(self):
            return list(items)

    class _FakeDB:
        def query(self, *_args, **_kwargs):
            return _FakeQuery()

    monkeypatch.setattr(tiered_feed_service, "apply_content_policy", lambda query, **_kwargs: query)
    monkeypatch.setattr(
        tiered_feed_service,
        "_get_surface_config",
        lambda _surface: {"fresh_hours": 168, "backfill_hours": 336, "evergreen_days": 30},
    )
    monkeypatch.setattr(tiered_feed_service, "make_default_policy", lambda **_kwargs: None)
    monkeypatch.setattr(
        tiered_feed_service,
        "build_surface_age_filters",
        lambda **_kwargs: SimpleNamespace(fresh=True, backfill=True, evergreen_tier=True),
    )
    monkeypatch.setattr(
        tiered_feed_service, "_get_recent_feedback_ids", lambda *_args: (set(), set())
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "_get_recent_negative_feedback",
        lambda *_args: ({1}, {"creator-2"}),
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "_count_accessible_fresh_items",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "mix_feed",
        lambda feed_items, surface, target_size, session_seed=None: list(feed_items)[:target_size],
    )
    monkeypatch.setattr(
        tiered_feed_service, "enforce_channel_caps", lambda feed_items, surface: feed_items
    )

    tiered_items, has_more, remaining = tiered_feed_service.get_tiered_feed(
        _FakeDB(),
        Surface.VIDEOS,
        limit=5,
        offset=0,
        now=now,
        hybrid_video_rerank=False,
        device_id="device-12345678",
    )

    assert [tiered.item.id for tiered in tiered_items] == [3]
    assert has_more is False
    assert remaining == 0


def test_get_cached_tiered_feed_hydrates_missing_video_durations(monkeypatch):
    now = datetime(2026, 3, 20, 12, 0, 0)
    item = SimpleNamespace(
        id=44,
        type=ContentType.VIDEO,
        title="Video",
        source_url="https://www.youtube.com/watch?v=test1234567A",
        video_url="https://www.youtube.com/watch?v=test1234567A",
        summary="summary",
        description="description",
        image_url=None,
        source="Trusted Source",
        created_at=now - timedelta(minutes=5),
        published_at=now - timedelta(minutes=10),
        global_score=0.5,
        promotion_score=0.4,
        recency_score=0.9,
        topics=["Technology"],
        channel_id="channel-1",
        acquisition_lane="search",
        source_status="discovery",
        views_per_hour=100.0,
        format_fit_score=1.0,
        promotion_reason="search",
        conversation_starters=None,
        duration_seconds=None,
    )
    tiered_item = TieredItem(
        item=item,
        tier=FreshnessTier.A,
        reason="fresh_published",
        published_age_seconds=600,
        added_age_seconds=300,
    )

    monkeypatch.setattr(tiered_feed_service, "_get_redis_client", lambda: None)
    monkeypatch.setattr(
        tiered_feed_service,
        "get_tiered_feed",
        lambda *args, **kwargs: ([tiered_item], False, 0),
    )
    monkeypatch.setattr(
        tiered_feed_service,
        "hydrate_missing_video_durations",
        lambda items, *, content_repo, **_kwargs: {items[0].id: 915},
    )

    items, has_more, meta = tiered_feed_service.get_cached_tiered_feed(
        db=SimpleNamespace(),
        surface=Surface.VIDEOS,
        limit=20,
        offset=0,
    )

    assert items[0]["duration_seconds"] == 915
    assert has_more is False
    assert meta.cache_hit is False


def test_get_cached_tiered_feed_preserves_cache_generated_at_on_hit(monkeypatch):
    generated_at = datetime(2026, 3, 30, 12, 0, 0)

    class _FakeRedis:
        def get(self, key):  # noqa: ARG002
            return json.dumps(
                {
                    "items": [
                        {
                            "id": 1,
                            "title": "Cached article",
                            "source_url": "https://example.com/article",
                            "published_at": "2026-03-30T11:00:00",
                            "created_at": "2026-03-30T11:05:00",
                        }
                    ],
                    "has_more": False,
                    "remaining_window_count": 0,
                    "generated_at": generated_at.isoformat(),
                }
            )

    monkeypatch.setattr(tiered_feed_service, "_get_redis_client", lambda: _FakeRedis())

    items, has_more, meta = tiered_feed_service.get_cached_tiered_feed(
        db=SimpleNamespace(),
        surface=Surface.ARTICLES,
        limit=20,
        offset=0,
    )

    assert items[0]["id"] == 1
    assert has_more is False
    assert meta.cache_hit is True
    assert meta.generated_at == generated_at
