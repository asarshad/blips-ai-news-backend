from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import content_event_dispatcher as dispatcher_module
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
from app.services.content_ai_service import CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
from app.services.content_event_dispatcher import ContentEventDispatcher
from app.services.content_promotion_service import CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_claim_article_image_events_prioritizes_newest_content():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    older = ContentItem(
        type=ContentType.ARTICLE,
        source="Old",
        title="Old article",
        source_url="https://example.com/old",
        published_at=datetime.utcnow() - timedelta(days=3),
    )
    newer = ContentItem(
        type=ContentType.ARTICLE,
        source="New",
        title="New article",
        source_url="https://example.com/new",
        published_at=datetime.utcnow(),
    )
    db.add_all([older, newer])
    db.commit()

    old_event = ContentEventOutbox(
        content_item_id=older.id,
        event_type=ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
        payload={"content_id": older.id},
        status="pending",
        available_at=datetime.utcnow(),
        created_at=datetime.utcnow() - timedelta(hours=1),
        updated_at=datetime.utcnow() - timedelta(hours=1),
    )
    new_event = ContentEventOutbox(
        content_item_id=newer.id,
        event_type=ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
        payload={"content_id": newer.id},
        status="pending",
        available_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([old_event, new_event])
    db.commit()

    dispatcher = ContentEventDispatcher(
        session_factory=SessionLocal,
        event_types=(ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,),
    )

    assert dispatcher._claim_pending(limit=1) == [new_event.id]


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


def test_dispatch_clustering_event_runs_clustering_job(monkeypatch):
    import sys
    from types import ModuleType

    triggers = []

    fake_module = ModuleType("app.scheduler.tasks_curation")
    fake_module.run_clustering_job = lambda *, trigger="scheduled": triggers.append(trigger)
    fake_module.CONTENT_CLUSTERING_REQUESTED_EVENT_TYPE = "content.clustering.requested"
    monkeypatch.setitem(sys.modules, "app.scheduler.tasks_curation", fake_module)

    dispatcher = ContentEventDispatcher()
    event = SimpleNamespace(
        event_type="content.clustering.requested",
        content_item_id=None,
        payload={"trigger": "fetch_news"},
    )

    dispatcher._dispatch_clustering_event(event)

    assert triggers == ["fetch_news"]


def test_ai_provider_quota_failures_back_off_for_an_hour():
    delay = dispatcher_module._retry_delay(
        3,
        event_type=CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
        error_message="Error code: 429 - insufficient_quota",
    )

    assert delay == dispatcher_module.timedelta(hours=1)
    assert delay > dispatcher_module.timedelta(minutes=5)


def test_ai_timezone_compare_failures_back_off_for_an_hour():
    assert dispatcher_module._retry_delay(
        1,
        event_type=CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
        error_message="can't compare offset-naive and offset-aware datetimes",
    ) == dispatcher_module.timedelta(hours=1)


def test_process_claimed_marks_promotion_processed(monkeypatch):
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
        title="Queued promotion",
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

    dispatcher = ContentEventDispatcher(session_factory=SessionLocal)

    assert dispatcher._process_claimed(event.id) is True

    refreshed = SessionLocal().get(ContentEventOutbox, event.id)

    assert refreshed is not None
    assert refreshed.status == "processed"
    assert processed and processed[0][1] == item.id
    assert refreshed.locked_at is None
    assert refreshed.processed_at is not None
