from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import content_event_dispatcher as dispatcher_module
from app.services.content_ai_service import CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
from app.services.content_promotion_service import CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE
from app.services.content_event_dispatcher import ContentEventDispatcher
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_dispatch_ready_event_invalidates_cache_and_triggers_auto_push(monkeypatch):
    invalidated = []
    pushes = []
    refreshed = []

    monkeypatch.setattr(
        dispatcher_module,
        "invalidate_tiered_feed_cache",
        lambda surface=None: invalidated.append(surface.value if surface else None),
    )
    monkeypatch.setattr(
        dispatcher_module,
        "refresh_cached_playlist_items",
        lambda db, *, content_ids: refreshed.append((db, content_ids)) or {"cache_keys_updated": 1},
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
    assert refreshed == [(db, [42])]
    assert pushes == [(db, [42], "event:content.ready")]


def test_dispatch_unready_event_invalidates_cache_without_push(monkeypatch):
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

    dispatcher._dispatch_unready_event(event)

    assert invalidated == ["articles", "videos"]
    assert pushes == []


def test_dispatch_article_image_verification_event_repairs_article(monkeypatch):
    repaired = []
    refreshed = []

    monkeypatch.setattr(
        dispatcher_module,
        "process_article_image_verification_request",
        lambda db, *, content_id: repaired.append((db, content_id))
        or {
            "content_id": content_id,
            "changed": True,
            "article_image_status": "VERIFIED",
            "readiness_status": "READY",
        },
    )
    monkeypatch.setattr(
        dispatcher_module,
        "refresh_cached_playlist_items",
        lambda db, *, content_ids: refreshed.append((db, content_ids)) or {"cache_keys_updated": 1},
    )

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        event_type=ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
        content_item_id=42,
        payload={"content_id": 42},
    )
    db = object()

    dispatcher._dispatch_article_image_verification_event(event, db)

    assert repaired == [(db, 42)]
    assert refreshed == [(db, [42])]


def test_dispatch_content_ai_summary_event_processes_one_content_item(monkeypatch):
    processed = []

    monkeypatch.setattr(
        dispatcher_module,
        "process_content_ai_summary_request",
        lambda db, *, content_id: processed.append((db, content_id))
        or {
            "content_id": content_id,
            "changed": True,
            "skipped": False,
            "ai_processed": True,
            "readiness_status": "READY",
        },
    )

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        event_type=CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
        content_item_id=77,
        payload={"content_id": 77},
    )
    db = object()

    dispatcher._dispatch_content_ai_summary_event(event, db)

    assert processed == [(db, 77)]


def test_dispatch_content_promotion_event_processes_one_content_item(monkeypatch):
    processed = []

    monkeypatch.setattr(
        dispatcher_module,
        "process_content_promotion_request",
        lambda db, *, content_id: processed.append((db, content_id))
        or {
            "content_id": content_id,
            "changed": True,
            "skipped": False,
            "promoted": True,
            "promotion_score": 0.91,
        },
    )

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        event_type=CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
        content_item_id=88,
        payload={"content_id": 88},
    )
    db = object()

    dispatcher._dispatch_content_promotion_event(event, db)

    assert processed == [(db, 88)]


def test_dispatch_content_promotion_event_defers_while_fetch_news_is_active(monkeypatch):
    monkeypatch.setattr(dispatcher_module, "_is_fetch_news_active", lambda: True)

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        event_type=CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
        content_item_id=88,
        payload={"content_id": 88},
    )

    with pytest.raises(dispatcher_module._DeferredDispatch) as exc_info:
        dispatcher._dispatch_content_promotion_event(event, object())

    assert exc_info.value.reason == "fetch_news_active"


def test_process_claimed_requeues_deferred_promotion_without_counting_failure(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/deferred-promotion",
        canonical_url="https://example.com/deferred-promotion",
        published_at=datetime.utcnow(),
        title="Deferred promotion",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    db.flush()
    event = ContentEventOutbox(
        content_item_id=item.id,
        event_type=CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
        payload={"content_id": item.id},
        status="processing",
        attempt_count=1,
        available_at=datetime.utcnow(),
        locked_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()

    monkeypatch.setattr(dispatcher_module, "_is_fetch_news_active", lambda: True)
    monkeypatch.setenv("CONTENT_PROMOTION_FETCH_DEFERRAL_SECONDS", "45")

    dispatcher = ContentEventDispatcher(session_factory=SessionLocal)

    assert dispatcher._process_claimed(event.id) is False

    refreshed = SessionLocal().get(ContentEventOutbox, event.id)

    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.locked_at is None
    assert refreshed.last_error is None
    assert refreshed.attempt_count == 0
    assert refreshed.available_at > datetime.utcnow()
