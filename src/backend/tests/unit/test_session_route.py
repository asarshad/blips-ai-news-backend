from __future__ import annotations

from types import SimpleNamespace

from app.api.routes import session as session_module
from app.models.content import ContentType


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
        device_id="device-12345678",
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
        device_id="device-abcdefgh",
        personalization_service=personalization_service,
        playlist_service=playlist_service,
    )

    assert response.success is True
    assert playlist_invalidations == ["device-abcdefgh"]
    assert invalidations == [("reels", "device-abcdefgh")]
