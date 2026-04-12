from __future__ import annotations

from types import SimpleNamespace

from fastapi import Response

from app.api.routes import session as session_module
from app.core.session_auth import AuthenticatedSession
from app.models.content import ContentType, EventType


def _session(device_id: str) -> AuthenticatedSession:
    return AuthenticatedSession(
        device_id=device_id,
        platform="ios",
        app_version="1.0.0",
        session_expires_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )


def test_record_interaction_invalidates_tiered_cache_for_less_from_creator(monkeypatch):
    invalidations = []
    playlist_invalidations = []

    monkeypatch.setattr(
        session_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidations.append((surface.value, device_id)),
    )

    personalization_service = SimpleNamespace(
        record_interaction=lambda **_kwargs: SimpleNamespace(id=101),
        content_repo=SimpleNamespace(
            get_by_id=lambda _content_item_id: SimpleNamespace(
                type=ContentType.VIDEO,
                video_url="https://www.youtube.com/watch?v=abc123",
                source_url="https://www.youtube.com/watch?v=abc123",
                canonical_url=None,
            )
        ),
    )
    playlist_service = SimpleNamespace(
        invalidate_user_cache=lambda device_id: playlist_invalidations.append(device_id)
    )

    request = session_module.InteractionRequest(
        content_item_id=7,
        event_type=session_module.EventTypeParam.LESS_FROM_CREATOR,
        extra_data=None,
    )

    response = session_module.record_interaction(
        request,
        session=_session("device-12345678"),
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-12345678"]
    assert invalidations == [
        ("videos", "device-12345678"),
        ("reels", "device-12345678"),
    ]


def test_record_interaction_invalidates_tiered_cache_for_fast_skip(monkeypatch):
    invalidations = []
    playlist_invalidations = []

    monkeypatch.setattr(
        session_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidations.append((surface.value, device_id)),
    )

    personalization_service = SimpleNamespace(
        record_interaction=lambda **_kwargs: SimpleNamespace(id=102),
        content_repo=SimpleNamespace(
            get_by_id=lambda _content_item_id: SimpleNamespace(
                type=ContentType.REEL,
                video_url="https://www.youtube.com/shorts/abc123",
                source_url="https://www.youtube.com/shorts/abc123",
                canonical_url=None,
            )
        ),
    )
    playlist_service = SimpleNamespace(
        invalidate_user_cache=lambda device_id: playlist_invalidations.append(device_id)
    )

    request = session_module.InteractionRequest(
        content_item_id=8,
        event_type=session_module.EventTypeParam.VIDEO_SKIP_LT_2S,
        extra_data=None,
    )

    response = session_module.record_interaction(
        request,
        session=_session("device-abcdefgh"),
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-abcdefgh"]
    assert invalidations == [("reels", "device-abcdefgh")]


def test_record_interaction_invalidates_article_tiered_cache_for_view_demotion(monkeypatch):
    invalidations = []
    playlist_invalidations = []

    monkeypatch.setattr(
        session_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidations.append((surface.value, device_id)),
    )
    monkeypatch.setattr(
        session_module.feed_freshness_strategies,
        "resolve",
        lambda _surface: SimpleNamespace(
            feedback_signals=lambda **_kwargs: SimpleNamespace(
                consumed_event_types=set(),
                exposed_event_types={EventType.VIEW_10S},
            )
        ),
    )

    personalization_service = SimpleNamespace(
        record_interaction=lambda **_kwargs: SimpleNamespace(id=103),
        content_repo=SimpleNamespace(
            get_by_id=lambda _content_item_id: SimpleNamespace(
                type=ContentType.ARTICLE,
                video_url=None,
                source_url="https://example.com/article",
                canonical_url="https://example.com/article",
            )
        ),
    )
    playlist_service = SimpleNamespace(
        invalidate_user_cache=lambda device_id: playlist_invalidations.append(device_id)
    )

    request = session_module.InteractionRequest(
        content_item_id=9,
        event_type=session_module.EventTypeParam.VIEW_10S,
        extra_data=None,
    )

    response = session_module.record_interaction(
        request,
        session=_session("device-fresh-1"),
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-fresh-1"]
    assert invalidations == [("articles", "device-fresh-1")]


def test_record_interaction_invalidates_video_tiered_cache_for_video_impression(monkeypatch):
    invalidations = []
    playlist_invalidations = []

    monkeypatch.setattr(
        session_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidations.append((surface.value, device_id)),
    )

    personalization_service = SimpleNamespace(
        record_interaction=lambda **_kwargs: SimpleNamespace(id=104),
        content_repo=SimpleNamespace(
            get_by_id=lambda _content_item_id: SimpleNamespace(
                type=ContentType.VIDEO,
                video_url="https://www.youtube.com/watch?v=abc123",
                source_url="https://www.youtube.com/watch?v=abc123",
                canonical_url=None,
            )
        ),
    )
    playlist_service = SimpleNamespace(
        invalidate_user_cache=lambda device_id: playlist_invalidations.append(device_id)
    )

    request = session_module.InteractionRequest(
        content_item_id=10,
        event_type=session_module.EventTypeParam.VIDEO_IMPRESSION,
        extra_data=None,
    )

    response = session_module.record_interaction(
        request,
        session=_session("device-video-impression"),
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-video-impression"]
    assert invalidations == [("videos", "device-video-impression")]


def test_record_interaction_invalidates_reel_tiered_cache_for_video_impression(monkeypatch):
    invalidations = []
    playlist_invalidations = []

    monkeypatch.setattr(
        session_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidations.append((surface.value, device_id)),
    )

    personalization_service = SimpleNamespace(
        record_interaction=lambda **_kwargs: SimpleNamespace(id=105),
        content_repo=SimpleNamespace(
            get_by_id=lambda _content_item_id: SimpleNamespace(
                type=ContentType.REEL,
                video_url="https://www.youtube.com/shorts/abc123",
                source_url="https://www.youtube.com/shorts/abc123",
                canonical_url=None,
            )
        ),
    )
    playlist_service = SimpleNamespace(
        invalidate_user_cache=lambda device_id: playlist_invalidations.append(device_id)
    )

    request = session_module.InteractionRequest(
        content_item_id=11,
        event_type=session_module.EventTypeParam.VIDEO_IMPRESSION,
        extra_data=None,
    )

    response = session_module.record_interaction(
        request,
        session=_session("device-reel-impression"),
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-reel-impression"]
    assert invalidations == [("reels", "device-reel-impression")]


class _CountDeleteQuery:
    def __init__(self, *, count_value: int = 0, delete_value: int = 0):
        self._count_value = count_value
        self._delete_value = delete_value

    def filter(self, *_args, **_kwargs):
        return self

    def count(self):
        return self._count_value

    def delete(self, **_kwargs):
        return self._delete_value


class _DeleteMyDataDB:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        if model is session_module.PushSubscription:
            return _CountDeleteQuery(count_value=2)
        if model is session_module.Usage:
            return _CountDeleteQuery(delete_value=3)
        if model is session_module.InteractionEvent:
            return _CountDeleteQuery(delete_value=4)
        if model is session_module.UserPreference:
            return _CountDeleteQuery(delete_value=5)
        if model is session_module.DeviceSession:
            return _CountDeleteQuery(delete_value=1)
        if model is session_module.UserProfile:
            return _CountDeleteQuery(delete_value=1)
        raise AssertionError(f"Unexpected model queried: {model}")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_delete_my_data_reports_push_subscriptions_and_total_rows():
    db = _DeleteMyDataDB()

    response = session_module.delete_my_data(session=_session("device-12345678"), db=db)

    assert db.committed is True
    assert db.rolled_back is False
    assert response.success is True
    assert response.deleted == {
        "push_subscriptions": 2,
        "usage": 3,
        "interaction_events": 4,
        "user_preferences": 5,
        "device_sessions": 1,
        "user_profiles": 1,
    }
    assert response.message == "Deleted 16 records across 6 tables."


def test_get_playlist_uses_current_page_dates_for_headers_and_body(monkeypatch):
    monkeypatch.setattr(session_module, "check_and_trigger_topup", lambda *_args, **_kwargs: None)

    playlist_service = SimpleNamespace(
        get_playlist=lambda **_kwargs: {
            "items": [
                {
                    "id": 11,
                    "type": "ARTICLE",
                    "title": "Current page article",
                    "source": "Example",
                    "source_url": "https://example.com/article-11",
                    "description": None,
                    "summary": "summary",
                    "image_url": None,
                    "video_url": None,
                    "duration": None,
                    "topics": ["Technology"],
                    "entities": [],
                    "published_at": "2026-03-31T10:00:00",
                    "created_at": "2026-03-31T10:05:00",
                    "global_score": None,
                    "cluster_id": None,
                }
            ],
            "session_id": "abcd1234",
            "cursor": 1,
            "has_more": True,
            "total_items": 100,
            "inventory_state": "healthy",
            "served_at": "2026-03-31T12:00:00",
            "feed_version": "feed-v1",
            "newest_published_at": "2026-03-31T12:59:00",
            "newest_created_at": "2026-03-31T13:00:00",
            "remaining_count": 99,
            "source": "redis",
            "cache_key": "playlist:test",
            "cache_hit": True,
            "freshness_strategy": "fresh_unseen_v1",
            "freshness_strategy_source": "redis",
            "resume_continuity_window_minutes": 10,
            "resume_snapshot_after_remote_window": False,
        }
    )

    response = Response()
    body = session_module.get_playlist(
        response=response,
        type=session_module.ContentTypeParam.ARTICLE,
        size=20,
        session_id=None,
        cursor=None,
        refresh=False,
        session=_session("device-12345678"),
        db=SimpleNamespace(),
        playlist_service=playlist_service,
    )

    assert body.newest_published_at == "2026-03-31T10:00:00"
    assert body.newest_created_at == "2026-03-31T10:05:00"
    assert response.headers["X-Newest-Published-At"] == "2026-03-31T10:00:00"
    assert response.headers["X-Newest-Created-At"] == "2026-03-31T10:05:00"
    assert response.headers["X-Freshness-Strategy"] == "fresh_unseen_v1"
    assert response.headers["X-Freshness-Strategy-Source"] == "redis"
    assert response.headers["X-Resume-Window-Minutes"] == "10"
    assert response.headers["X-Resume-Snapshot-After-Window"] == "false"
    assert body.freshness_strategy_source == "redis"
    assert body.resume_continuity_window_minutes == 10
    assert body.resume_snapshot_after_remote_window is False


def test_get_playlist_metadata_returns_lightweight_head_fields_and_headers():
    playlist_service = SimpleNamespace(
        get_playlist_metadata=lambda **_kwargs: {
            "served_at": "2026-04-11T17:15:00",
            "feed_version": "feed-v2",
            "newest_published_at": "2026-04-11T17:10:00",
            "newest_created_at": "2026-04-11T17:12:00",
            "freshness_strategy": "fresh_unseen_v1",
            "freshness_strategy_source": "env",
            "source": "redis",
            "cache_key": "playlist:test:meta",
            "cache_hit": True,
        }
    )

    response = Response()
    body = session_module.get_playlist_metadata(
        response=response,
        type=session_module.ContentTypeParam.ARTICLE,
        session=_session("device-meta-1"),
        playlist_service=playlist_service,
    )

    assert body.feed_version == "feed-v2"
    assert body.newest_published_at == "2026-04-11T17:10:00"
    assert body.newest_created_at == "2026-04-11T17:12:00"
    assert body.freshness_strategy == "fresh_unseen_v1"
    assert body.freshness_strategy_source == "env"
    assert response.headers["X-Feed-Version"] == "feed-v2"
    assert response.headers["X-Freshness-Strategy"] == "fresh_unseen_v1"
    assert response.headers["X-Freshness-Strategy-Source"] == "env"
    assert response.headers["X-Newest-Published-At"] == "2026-04-11T17:10:00"
    assert response.headers["X-Newest-Created-At"] == "2026-04-11T17:12:00"
