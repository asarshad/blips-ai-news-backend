from __future__ import annotations

from types import SimpleNamespace

from app.services import content_event_dispatcher as dispatcher_module
from app.services.content_event_dispatcher import ContentEventDispatcher


def test_dispatch_ready_event_invalidates_cache_and_triggers_auto_push(monkeypatch):
    invalidated = []
    pushes = []

    monkeypatch.setattr(
        dispatcher_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None: invalidated.append(surface.value if surface else None),
    )

    class _FakePushNotificationService:
        def __init__(self, db):
            self._db = db

        def send_auto_for_content_ids(self, content_ids, *, actor):
            pushes.append((self._db, content_ids, actor))
            return []

    monkeypatch.setattr(
        dispatcher_module,
        "PushNotificationService",
        _FakePushNotificationService,
    )

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        content_item_id=42,
        payload={"content_id": 42, "surfaces": ["articles", "videos"]},
    )
    db = object()

    dispatcher._dispatch_ready_event(event, db)

    assert invalidated == ["articles", "videos"]
    assert pushes == [(db, [42], "event:content.ready")]
