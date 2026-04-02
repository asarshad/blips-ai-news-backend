from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentType
from app.services.playlist_service import PlaylistService


class _RankingStub:
    def __init__(self):
        self.calls = []

    def score_item(self, item, *, personalization_score: float = 0.0) -> float:
        self.calls.append((item.id, personalization_score))
        return float(item.id) + float(personalization_score)


def _item(
    id_: int, *, source: str = "techcrunch", channel_id: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        source=source,
        channel_id=channel_id or source,
        global_score=0.5,
        promotion_score=0.5,
        editorial_boost=0,
        topics=["AI"],
        cluster_id=f"c{id_}",
    )


def test_score_candidates_uses_ranking_service_and_sorts_descending():
    ranking = _RankingStub()
    personalization = MagicMock()
    personalization.compute_personalization_scores.return_value = {1: 0.1, 2: 0.2}

    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=ranking,
        redis_client=None,
    )

    scored = service._score_candidates("device-1", [_item(1), _item(2)])

    personalization.compute_personalization_scores.assert_called_once()
    assert ranking.calls == [(1, 0.1), (2, 0.2)]
    assert [item.id for item, _ in scored] == [2, 1]


def test_generate_playlist_items_excludes_consumed_and_demotes_exposed():
    ranking = _RankingStub()
    personalization = MagicMock()
    personalization.compute_personalization_scores.return_value = {1: 0.0, 2: 0.0, 3: 0.0}
    interaction_repo = MagicMock()
    interaction_repo.get_recent_feedback_ids.return_value = ({2}, {1})
    interaction_repo.get_recent_negative_feedback.return_value = (set(), set())
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = [_item(1), _item(2), _item(3)]

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        interaction_repo=interaction_repo,
        ranking_service=ranking,
        redis_client=None,
    )

    selected = service._generate_playlist_items("device-1", ContentType.VIDEO, 3)

    assert [item.id for item in selected] == [3, 1]


def test_generate_playlist_items_prefers_unseen_pool_before_exposed_fill():
    ranking = _RankingStub()
    personalization = MagicMock()
    personalization.compute_personalization_scores.return_value = {
        1: 0.0,
        2: 0.0,
        3: 0.0,
        4: 0.0,
    }
    interaction_repo = MagicMock()
    interaction_repo.get_recent_feedback_ids.return_value = (set(), {4})
    interaction_repo.get_recent_negative_feedback.return_value = (set(), set())
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = [
        _item(1),
        _item(2),
        _item(3),
        _item(4),
    ]

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        interaction_repo=interaction_repo,
        ranking_service=ranking,
        redis_client=None,
    )

    selected = service._generate_playlist_items("device-1", ContentType.VIDEO, 3)

    assert [item.id for item in selected] == [3, 2, 1]


def test_generate_playlist_items_filters_negative_item_and_creator_feedback():
    ranking = _RankingStub()
    personalization = MagicMock()
    personalization.compute_personalization_scores.return_value = {1: 0.0, 2: 0.0, 3: 0.0}
    interaction_repo = MagicMock()
    interaction_repo.get_recent_feedback_ids.return_value = (set(), set())
    interaction_repo.get_recent_negative_feedback.return_value = ({1}, {"creator-2"})
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = [
        _item(1, source="Source A", channel_id="creator-1"),
        _item(2, source="Source B", channel_id="creator-2"),
        _item(3, source="Source C", channel_id="creator-3"),
    ]

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        interaction_repo=interaction_repo,
        ranking_service=ranking,
        redis_client=None,
    )

    selected = service._generate_playlist_items("device-1", ContentType.VIDEO, 3)

    assert [item.id for item in selected] == [3]


def test_playlist_cache_keys_separate_freshness_strategies():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    current_key = service._get_cache_key(
        "device-1",
        ContentType.ARTICLE,
        strategy_name="current",
    )
    fresh_key = service._get_cache_key(
        "device-1",
        ContentType.ARTICLE,
        strategy_name="fresh_unseen_v1",
    )

    assert current_key != fresh_key
    assert current_key.endswith(":ARTICLE:s:current")
    assert fresh_key.endswith(":ARTICLE:s:fresh_unseen_v1")


def test_playlist_session_cache_key_stays_stable_across_strategy_flips():
    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )

    session_key = service._get_session_cache_key(
        "device-1",
        ContentType.ARTICLE,
        "session-1234",
    )

    assert session_key.endswith(":ARTICLE:session-1234")


def test_ensure_snapshot_depth_keeps_snapshot_strategy_when_extending(monkeypatch):
    service = PlaylistService(
        content_repo=MagicMock(db=object()),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=MagicMock(),
        redis_client=None,
    )
    service._supports_tiered_snapshots = lambda: True

    recorded = {}

    def _fake_get_cached_tiered_feed(
        _db,
        _surface,
        *,
        limit,
        offset,
        hybrid_video_rerank=False,
        device_id=None,
        strategy=None,
    ):
        recorded["strategy_name"] = getattr(strategy, "name", None)
        return (
            [
                {
                    "id": 2,
                    "type": "ARTICLE",
                    "source": "Example",
                    "source_url": "https://example.com/2",
                    "title": "Article 2",
                    "description": None,
                    "summary": "summary",
                    "image_url": None,
                    "video_url": None,
                    "duration": None,
                    "topics": ["Technology"],
                    "entities": [],
                    "published_at": "2026-04-01T10:00:00",
                    "created_at": "2026-04-01T10:05:00",
                    "conversation_starters": {},
                }
            ],
            False,
            SimpleNamespace(remaining_window_count=0),
        )

    monkeypatch.setattr(
        "app.services.playlist_service.get_cached_tiered_feed",
        _fake_get_cached_tiered_feed,
    )

    snapshot = {
        "items": [
            {
                "id": 1,
                "type": "ARTICLE",
                "source": "Example",
                "source_url": "https://example.com/1",
                "title": "Article 1",
                "description": None,
                "summary": "summary",
                "image_url": None,
                "video_url": None,
                "duration": None,
                "topics": ["Technology"],
                "entities": [],
                "published_at": "2026-04-01T11:00:00",
                "created_at": "2026-04-01T11:05:00",
                "conversation_starters": {},
            }
        ],
        "has_more": True,
        "remaining_count": 1,
        "freshness_strategy": "fresh_unseen_v1",
    }

    updated = service._ensure_snapshot_depth(
        device_id="device-1",
        content_type=ContentType.ARTICLE,
        snapshot=snapshot,
        required_count=2,
        cache_key="ignored",
    )

    assert recorded["strategy_name"] == "fresh_unseen_v1"
    assert [item["id"] for item in updated["items"]] == [1, 2]
