import json
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api.routes.session import PlaylistItem
from app.models.content import ContentType
from app.services.playlist_service import (
    FALLBACK_CONTENT_AGE_HOURS,
    MAX_CONTENT_AGE_HOURS,
    PlaylistService,
)


def _item(item_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        type=ContentType.ARTICLE,
        source="TechCrunch",
        source_url=f"https://example.com/{item_id}",
        title=f"Item {item_id}",
        description="desc",
        summary="summary",
        image_url=None,
        video_url=None,
        duration_seconds=None,
        topics=["AI"],
        entities=[],
        published_at=datetime.now(timezone.utc),
        global_score=0.8,
        cluster_id=f"c{item_id}",
        editorial_boost=0,
        conversation_starters={
            "starters": [f"Question for {item_id}?"],
            "fallback": ["Fallback?"],
        },
    )


class _RankingStub:
    def score_item(self, item, *, personalization_score: float = 0.0) -> float:
        return float(item.id) + float(personalization_score)


class _MemoryRedis:
    def __init__(self):
        self._values = {}

    def get(self, key):
        return self._values.get(key)

    def setex(self, key, ttl, value):  # noqa: ARG002
        self._values[key] = value


def test_get_playlist_falls_back_to_cached_snapshot_when_no_new_candidates():
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = []

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    redis = MagicMock()
    redis.get.side_effect = [
        None,  # miss on session cache
        json.dumps(
            [
                {
                    "id": 99,
                    "type": "ARTICLE",
                    "conversation_starters": {
                        "starters": ["Cached question?"],
                        "fallback": ["Cached fallback?"],
                    },
                }
            ]
        ),  # hit on latest fallback cache
    ]

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        redis_client=redis,
    )

    result = service.get_playlist("device-1", ContentType.ARTICLE, size=20)

    assert result["items"] == [
        {
            "id": 99,
            "type": "ARTICLE",
            "conversation_starters": {
                "starters": ["Cached question?"],
                "fallback": ["Cached fallback?"],
            },
        }
    ]
    assert result["total_items"] == 1
    assert result["has_more"] is False
    assert redis.setex.called


def test_get_playlist_expands_to_historical_window_when_recent_inventory_empty():
    recent_call = []

    def _get_items_for_playlist(*, content_type, hours_back, limit):
        recent_call.append(hours_back)
        if hours_back == MAX_CONTENT_AGE_HOURS:
            return []
        if hours_back == FALLBACK_CONTENT_AGE_HOURS:
            return [_item(10)]
        return []

    content_repo = MagicMock()
    content_repo.get_items_for_playlist.side_effect = _get_items_for_playlist

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.3

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        redis_client=None,
    )

    result = service.get_playlist("device-2", ContentType.ARTICLE, size=20)

    assert recent_call[0] == MAX_CONTENT_AGE_HOURS
    assert recent_call[1] == FALLBACK_CONTENT_AGE_HOURS
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == 10


def test_top_up_items_with_historical_fills_short_playlist():
    def _get_items_for_playlist(*, content_type, hours_back, limit):  # noqa: ARG001
        if hours_back == FALLBACK_CONTENT_AGE_HOURS:
            return [_item(2), _item(3), _item(4), _item(5)]
        return []

    content_repo = MagicMock()
    content_repo.get_items_for_playlist.side_effect = _get_items_for_playlist

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.2

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=_RankingStub(),
        redis_client=None,
    )

    topped = service._top_up_items_with_historical(
        device_id="device-3",
        content_type=ContentType.ARTICLE,
        selected_items=[_item(1)],
        target_size=4,
    )

    assert [item.id for item in topped] == [1, 5, 4, 3]


def test_format_item_includes_conversation_starters():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    formatted = service._format_item(_item(7))

    assert formatted["conversation_starters"] == {
        "starters": ["Question for 7?"],
        "fallback": ["Fallback?"],
    }


def test_format_item_coerces_missing_source_to_unknown():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    item = _item(70)
    item.source = None

    formatted = service._format_item(item)

    assert formatted["source"] == "Unknown"


def test_format_item_coerces_object_entities_to_string_terms():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    item = _item(71)
    item.entities = [{"name": "OpenAI", "type": "ORG"}, {"value": "Sam Altman"}]

    formatted = service._format_item(item)

    assert formatted["entities"] == ["OpenAI", "Sam Altman"]


def test_format_item_normalizes_payload_for_session_response_model():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    item = _item(72)
    item.source = None
    item.title = None
    item.entities = [{"name": "OpenAI"}]
    item.global_score = "0.42"
    item.cluster_id = 123
    item.conversation_starters = {"starters": "bad-shape", "fallback": ["Fallback?"]}

    formatted = service._format_item(item)
    payload = PlaylistItem.model_validate(formatted).model_dump()

    assert payload["source"] == "Unknown"
    assert payload["title"] == "Article from example.com"
    assert payload["entities"] == ["OpenAI"]
    assert payload["global_score"] == 0.42
    assert payload["cluster_id"] == "123"
    assert payload["conversation_starters"]["fallback"] == ["Fallback?"]


def test_format_item_uses_effective_reel_type_for_explicit_shorts_video():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    shorts_item = _item(8)
    shorts_item.type = ContentType.VIDEO
    shorts_item.source_url = "https://www.youtube.com/shorts/VvGaDPViMKY"
    shorts_item.video_url = "https://www.youtube.com/shorts/VvGaDPViMKY"

    formatted = service._format_item(shorts_item)

    assert formatted["type"] == ContentType.REEL.value


def test_get_playlist_hydrates_missing_video_durations(monkeypatch):
    content_repo = MagicMock()
    video_item = _item(12)
    video_item.type = ContentType.VIDEO
    video_item.source_url = "https://www.youtube.com/watch?v=test1234567A"
    video_item.video_url = "https://www.youtube.com/watch?v=test1234567A"
    video_item.duration_seconds = None
    content_repo.get_items_for_playlist.return_value = [video_item]

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    playlist_service_module = import_module("app.services.playlist_service")
    monkeypatch.setattr(
        playlist_service_module,
        "hydrate_missing_video_durations",
        lambda items, *, content_repo, **_kwargs: {items[0].id: 742},
    )

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=_RankingStub(),
        redis_client=None,
    )

    result = service.get_playlist("device-duration", ContentType.VIDEO, size=20)

    assert result["items"][0]["duration"] == 742


def test_get_playlist_discards_stale_cached_snapshot_missing_conversation_starters():
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = [_item(10)]

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    redis = MagicMock()
    redis.get.return_value = json.dumps(
        [
            {
                "id": 99,
                "type": "ARTICLE",
                "title": "Old snapshot",
            }
        ]
    )

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=_RankingStub(),
        redis_client=redis,
    )

    result = service.get_playlist("device-4", ContentType.ARTICLE, size=20)

    assert result["items"][0]["id"] == 10
    assert result["items"][0]["conversation_starters"]["starters"] == ["Question for 10?"]
    assert redis.setex.called


def test_get_playlist_extends_session_snapshot_beyond_initial_100_items(monkeypatch):
    content_repo = MagicMock()
    content_repo.db = MagicMock()
    content_repo.db.query = MagicMock()

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    from app.services import playlist_service as playlist_service_module

    calls = []

    def _fake_get_cached_tiered_feed(
        db,  # noqa: ARG001
        surface,  # noqa: ARG001
        limit,
        offset,
        hybrid_video_rerank,  # noqa: ARG001
        device_id,  # noqa: ARG001
    ):
        calls.append((limit, offset))
        if offset == 0:
            items = [
                {
                    "id": idx,
                    "title": f"Tiered item {idx}",
                    "source": "Tiered Source",
                    "source_url": f"https://example.com/{idx}",
                    "conversation_starters": {"starters": [], "fallback": []},
                }
                for idx in range(1, 101)
            ]
            meta = SimpleNamespace(
                generated_at=datetime.now(timezone.utc),
                source="db",
                cache_key="tiered:articles:0",
                cache_hit=False,
                remaining_window_count=60,
            )
            return items, True, meta
        if offset == 100:
            items = [
                {
                    "id": idx,
                    "title": f"Tiered item {idx}",
                    "source": "Tiered Source",
                    "source_url": f"https://example.com/{idx}",
                    "conversation_starters": {"starters": [], "fallback": []},
                }
                for idx in range(101, 151)
            ]
            meta = SimpleNamespace(
                generated_at=datetime.now(timezone.utc),
                source="db",
                cache_key="tiered:articles:100",
                cache_hit=False,
                remaining_window_count=10,
            )
            return items, False, meta
        raise AssertionError(f"unexpected offset {offset}")

    monkeypatch.setattr(
        playlist_service_module, "get_cached_tiered_feed", _fake_get_cached_tiered_feed
    )

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        redis_client=_MemoryRedis(),
    )
    service._supports_tiered_snapshots = lambda: True

    first_page = service.get_playlist("device-extend", ContentType.ARTICLE, size=50)
    second_page = service.get_playlist(
        "device-extend",
        ContentType.ARTICLE,
        size=50,
        session_id=first_page["session_id"],
        cursor=50,
    )
    third_page = service.get_playlist(
        "device-extend",
        ContentType.ARTICLE,
        size=20,
        session_id=first_page["session_id"],
        cursor=100,
    )
    final_page = service.get_playlist(
        "device-extend",
        ContentType.ARTICLE,
        size=30,
        session_id=first_page["session_id"],
        cursor=120,
    )

    assert first_page["has_more"] is True
    assert second_page["has_more"] is True
    assert third_page["has_more"] is True
    assert final_page["has_more"] is False
    assert third_page["total_items"] == 150
    assert [item["id"] for item in third_page["items"]] == list(range(101, 121))
    assert all(item["type"] == ContentType.ARTICLE.value for item in third_page["items"])
    assert calls == [(100, 0), (100, 100)]


def test_tiered_article_snapshot_items_are_normalized_for_session_schema(monkeypatch):
    content_repo = MagicMock()
    content_repo.db = MagicMock()
    content_repo.db.query = MagicMock()

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=_MemoryRedis(),
    )
    service._supports_tiered_snapshots = lambda: True

    from app.services import playlist_service as playlist_service_module

    def _fake_get_cached_tiered_feed(*args, **kwargs):  # noqa: ARG001
        items = [
            {
                "id": 501,
                "title": "[pending] https://example.com/posts/openai-rollout",
                "source": None,
                "source_url": "https://example.com/posts/openai-rollout",
                "summary": "Summary",
                "topics": [{"name": "AI"}],
                "entities": [{"name": "OpenAI"}],
                "global_score": "0.7",
                "published_at": "2026-03-22T00:00:00",
                "created_at": "2026-03-22T00:00:01",
            }
        ]
        meta = SimpleNamespace(
            generated_at=datetime.now(timezone.utc),
            source="db",
            cache_key="tiered:articles:0",
            cache_hit=False,
            remaining_window_count=0,
        )
        return items, False, meta

    monkeypatch.setattr(
        playlist_service_module,
        "get_cached_tiered_feed",
        _fake_get_cached_tiered_feed,
    )

    result = service.get_playlist("device-tiered-shape", ContentType.ARTICLE, size=20)
    payload = PlaylistItem.model_validate(result["items"][0]).model_dump()

    assert payload["type"] == ContentType.ARTICLE.value
    assert payload["source"] == "Unknown"
    assert payload["title"] == "OpenAI Rollout"
    assert payload["topics"] == ["AI"]
    assert payload["entities"] == ["OpenAI"]


def test_get_playlist_discards_stale_video_cache_containing_shorts_url():
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = [_item(11)]

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    redis = MagicMock()
    redis.get.return_value = json.dumps(
        [
            {
                "id": 17171,
                "type": "VIDEO",
                "title": "Leaked reel",
                "source_url": "https://www.youtube.com/shorts/VvGaDPViMKY",
                "video_url": "https://www.youtube.com/shorts/VvGaDPViMKY",
                "conversation_starters": {
                    "starters": ["Cached question?"],
                    "fallback": ["Cached fallback?"],
                },
            }
        ]
    )

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=_RankingStub(),
        redis_client=redis,
    )

    result = service.get_playlist("device-5", ContentType.VIDEO, size=20)

    assert result["items"][0]["id"] == 11
    assert redis.setex.called


def test_invalidate_user_cache_clears_legacy_and_video_reel_namespaces():
    redis = MagicMock()
    redis.keys.side_effect = [
        ["playlist:abc123:ARTICLE"],
        ["playlist:session:abc123:ARTICLE:abcd1234"],
        ["playlist:video-reel-v2:abc123:VIDEO"],
        ["playlist:video-reel-v2:session:abc123:VIDEO:efgh5678"],
    ]

    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=redis,
    )

    service.invalidate_user_cache("device-6")

    redis.delete.assert_called_once_with(
        "playlist:abc123:ARTICLE",
        "playlist:session:abc123:ARTICLE:abcd1234",
        "playlist:video-reel-v2:abc123:VIDEO",
        "playlist:video-reel-v2:session:abc123:VIDEO:efgh5678",
    )
