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


    result = sync_content_readiness(db, item)
    db.commit()

    outbox_rows = db.query(ContentEventOutbox).all()

    assert result.current_status.value == "PENDING"
    assert result.reason == "awaiting_ai_processing"
    assert len(outbox_rows) == 1
    assert outbox_rows[0].event_type == content_ai_service.CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
    assert outbox_rows[0].content_item_id == item.id


def test_sync_content_readiness_queues_video_ai_summary_when_flag_enabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Example Channel",
        source_url="https://example.com/watch?v=123",
        video_url="https://example.com/watch?v=123",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted video awaiting summary",
        description=("Video details about queues and retries. " * 20).strip(),
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()


    result = sync_content_readiness(db, item)
    db.commit()

    outbox_rows = db.query(ContentEventOutbox).all()

    assert result.current_status.value == "PENDING"
    assert result.reason == "awaiting_video_ai_processing"
    assert len(outbox_rows) == 1
    assert outbox_rows[0].event_type == content_ai_service.CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
    assert outbox_rows[0].content_item_id == item.id


def test_sync_content_readiness_does_not_queue_reel_ai_summary_when_flag_enabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.REEL,
        source="Example Channel",
        source_url="https://www.youtube.com/shorts/abc123",
        video_url="https://www.youtube.com/shorts/abc123",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted reel",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()


    result = sync_content_readiness(db, item)
    db.commit()

    outbox_rows = db.query(ContentEventOutbox).all()

    assert result.current_status.value == "READY"
    assert result.reason == "reel_ready"
    assert [row.event_type for row in outbox_rows] == ["content.ready"]


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


def test_process_article_summary_persists_tech_relevance_via_mark_ai_processed(monkeypatch):
    """Tech relevance fields must be passed explicitly to mark_ai_processed."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Reuters",
        source_url="https://reuters.com/tech/story",
        canonical_url="https://reuters.com/tech/story",
        published_at=_recent_dt(hours_ago=1),
        title="Apple's new chip changes everything",
        description="A long description about the new chip. " * 20,
        content_text="A long article body about the new chip. " * 20,
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(minutes_ago=10),
        updated_at=_recent_dt(minutes_ago=10),
    )
    db.add(item)
    db.commit()

    # Stub the article hydrator so it writes a valid summary without hitting LLM
    fake_hydrator = MagicMock()
    fake_hydrator.needs_retry_refresh = MagicMock(return_value=False)

    def _populate_summary(item, **_kwargs):
        item.summary = "Apple unveiled a new chip that significantly improves performance."
        item.topics = ["technology", "semiconductors"]
        item.conversation_starters = []

    fake_hydrator.populate_article_summary.side_effect = _populate_summary
    fake_hydrator.refresh_article_annotations = MagicMock()

    # Stub the LLM client with a tech-relevant classification response
    fake_llm = MagicMock()
    fake_llm.classify_blips_tech_relevance.return_value = SimpleNamespace(
        is_blips_tech_relevant="yes",
        confidence=0.97,
        reason="Directly about a major tech company product launch.",
    )

    mark_calls = []
    original_mark = content_ai_service.ContentItemRepository

    class PatchedRepo(original_mark):
        def mark_ai_processed(self, item_id, summary, topics=None, *, commit=True, **kwargs):
            mark_calls.append({"item_id": item_id, "summary": summary, **kwargs})
            return super().mark_ai_processed(
                item_id, summary, topics=topics, commit=commit, **kwargs
            )

    monkeypatch.setattr(content_ai_service, "ContentItemRepository", PatchedRepo)
    monkeypatch.setattr(content_ai_service, "ArticleHydrationService", lambda: fake_hydrator)

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
    )

    assert result["changed"] is True
    assert len(mark_calls) == 1
    assert mark_calls[0]["tech_relevance"] == "yes"
    assert mark_calls[0]["tech_relevance_confidence"] == 0.97
    assert mark_calls[0]["tech_relevance_reason"] == "Directly about a major tech company product launch."

    db.commit()
    refreshed = db.get(ContentItem, item.id)
    assert refreshed.tech_relevance == "yes"
    assert refreshed.tech_relevance_confidence == 0.97
