from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import content_ai_service
from app.services.content_readiness import sync_content_readiness


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _recent_dt(*, hours_ago: int = 0, minutes_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(hours=hours_ago, minutes=minutes_ago)


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_queue_content_ai_summary_request_dedupes_pending_rows():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=1),
        title="Queued article",
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_ai_processing",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    first = content_ai_service.queue_content_ai_summary_request(db, item)
    second = content_ai_service.queue_content_ai_summary_request(db, item)
    db.commit()

    rows = db.query(ContentEventOutbox).all()

    assert first is not None
    assert second is None
    assert len(rows) == 1
    assert rows[0].event_type == content_ai_service.CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE


def test_sync_content_readiness_queues_ai_summary_when_flag_enabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted article awaiting summary",
        content_text=("Release engineering details. " * 80).strip(),
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        "app.services.content_readiness.feature_flags.is_enabled",
        lambda feature: feature == "event_driven_ai",
    )

    result = sync_content_readiness(db, item)
    db.commit()

    outbox_rows = db.query(ContentEventOutbox).all()

    assert result.current_status.value == "PENDING"
    assert result.reason == "awaiting_ai_processing"
    assert len(outbox_rows) == 1
    assert outbox_rows[0].event_type == content_ai_service.CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
    assert outbox_rows[0].content_item_id == item.id


def test_process_content_ai_summary_request_skips_already_processed_item():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Already processed article",
        summary="Existing summary already present.",
        ai_processed=True,
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="READY",
        readiness_reason="article_ready",
        ready_at=_recent_dt(hours_ago=1),
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    result = content_ai_service.process_content_ai_summary_request(db, content_id=item.id)

    assert result["skipped"] is True
    assert result["reason"] == "already_processed"


def test_process_content_ai_summary_request_marks_article_ready(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted article",
        content_text=("Detailed summary input about worker queues and retries. " * 40).strip(),
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_ai_processing",
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    invalidated = []
    refreshed = []

    monkeypatch.setattr(
        content_ai_service,
        "invalidate_tiered_feed_cache",
        lambda surface=None, device_id=None: invalidated.append((surface, device_id)),
    )
    monkeypatch.setattr(
        content_ai_service,
        "refresh_cached_playlist_items",
        lambda db, *, content_ids: refreshed.append(content_ids) or {"cache_keys_updated": 1},
    )

    class _FakeLLMClient:
        def is_configured(self):
            return True

        def get_provider(self):
            return "fake"

    class _FakeArticleHydrator:
        def populate_article_summary(self, target, *, precompute_starter_answers=False):
            target.summary = (
                "This event-driven article summary is intentionally long enough to satisfy "
                "the persisted summary threshold while keeping the test deterministic."
            )
            target.topics = ["queues", "workers"]
            target.ai_processed = True
            return True

        def refresh_article_annotations(self, target):
            return None

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=_FakeLLMClient(),
        article_hydrator=_FakeArticleHydrator(),
    )
    db.commit()

    refreshed_item = db.get(ContentItem, item.id)
    outbox_rows = db.query(ContentEventOutbox).all()

    assert result["changed"] is True
    assert refreshed_item.ai_processed is True
    assert refreshed_item.readiness_status == "READY"
    assert refreshed_item.readiness_reason == "article_ready"
    assert any(row.event_type == "content.ready" for row in outbox_rows)
    assert invalidated == [(None, None)]
    assert refreshed == [[item.id]]
