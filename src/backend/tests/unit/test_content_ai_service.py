from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.models.ingestion_budget import IngestionBudget
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
    IngestionBudget.__table__.create(bind=engine)


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


def test_queue_content_ai_summary_request_allows_exhausted_article_retry_terminalization():
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
        summary=f"__blips_article_retry__:v1:3:{_recent_dt(hours_ago=1).isoformat()}",
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="article_unskimmable_retry",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    event = content_ai_service.queue_content_ai_summary_request(db, item)
    db.commit()

    assert event is not None
    assert db.query(ContentEventOutbox).count() == 1


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


def test_process_content_ai_summary_request_skips_exhausted_article_retry_window():
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
        title="Retry exhausted article",
        summary=f"__blips_article_retry__:v1:3:{_recent_dt(hours_ago=1).isoformat()}",
        ai_processed=False,
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="article_unskimmable_retry",
        ingestion_day=date.today(),
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.add(
        IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=10,
            inserted=5,
            reserved=0,
            seen=5,
            suppressed=0,
            attempts=5,
        )
    )
    db.commit()

    result = content_ai_service.process_content_ai_summary_request(db, content_id=item.id)
    db.commit()

    refreshed_item = db.get(ContentItem, item.id)

    assert result["skipped"] is False
    assert result["reason"] == "article_unskimmable_terminal"
    assert result["changed"] is True
    assert refreshed_item.is_suppressed is True
    assert refreshed_item.curation_status == ContentStatus.CANDIDATE
    assert refreshed_item.summary is None
    assert refreshed_item.ai_processed is True
    assert refreshed_item.readiness_reason == "suppressed"
    budget = db.get(IngestionBudget, (date.today(), ContentType.ARTICLE))
    assert budget.inserted == 4


def test_process_content_ai_summary_request_rechecks_text_before_terminal_reject(monkeypatch):
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
        title="Retry exhausted article now skimmable",
        content_text=(
            "Recovered article text about AI infrastructure and worker queues. " * 40
        ).strip(),
        summary=f"__blips_article_retry__:v1:3:{_recent_dt(hours_ago=1).isoformat()}",
        ai_processed=False,
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="article_unskimmable_retry",
        ingestion_day=date.today(),
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.add(
        IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=10,
            inserted=5,
            reserved=0,
            seen=5,
            suppressed=0,
            attempts=5,
        )
    )
    db.commit()

    class _FakeLLMClient:
        def is_configured(self):
            return True

        def get_provider(self):
            return "fake"

    class _FakeArticleHydrator:
        def populate_article_summary(self, target, **_kwargs):
            target.summary = (
                "Recovered article summary is now long enough to satisfy the ready "
                "threshold after source text became available during retry."
            )
            target.topics = ["ai", "infrastructure"]
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
    budget = db.get(IngestionBudget, (date.today(), ContentType.ARTICLE))

    assert result["changed"] is True
    assert result["reason"] is None
    assert refreshed_item.is_suppressed is False
    assert refreshed_item.curation_status == ContentStatus.PROMOTED
    assert refreshed_item.ai_processed is True
    assert refreshed_item.readiness_status == "READY"
    assert refreshed_item.readiness_reason == "article_ready"
    assert budget.inserted == 5


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
        def populate_article_summary(
            self,
            target,
            *,
            precompute_starter_answers=False,
            **_kwargs,
        ):
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


def test_process_content_ai_summary_request_skips_non_tech_video_before_summary(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Reuters",
        source_url="https://example.com/watch?v=999",
        video_url="https://example.com/watch?v=999",
        published_at=_recent_dt(hours_ago=1),
        title="Downtown protest after local political dispute",
        description="General world news coverage with no meaningful tech angle.",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_video_ai_processing",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    fake_llm = SimpleNamespace(
        is_configured=lambda: True,
        get_provider=lambda: "fake",
        classify_blips_tech_relevance=lambda **kwargs: SimpleNamespace(
            is_blips_tech_relevant="no",
            confidence=0.97,
            reason="General political protest with no meaningful tech connection.",
        ),
        summarize_video=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("summarize_video should not run for non-tech video")
        ),
    )

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
    )
    db.commit()

    refreshed_item = db.get(ContentItem, item.id)
    assert result["changed"] is True
    assert refreshed_item.ai_processed is True
    assert refreshed_item.summary is None
    assert refreshed_item.tech_relevance == "none"
    assert refreshed_item.tech_relevance_confidence == 0.97
    assert refreshed_item.readiness_status == "PENDING"
    assert refreshed_item.readiness_reason == "video_non_tech"


def test_process_content_ai_summary_request_records_video_summary_retry(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Example Channel",
        source_url="https://example.com/watch?v=retry",
        video_url="https://example.com/watch?v=retry",
        published_at=_recent_dt(hours_ago=1),
        title="Tech video with empty LLM summary",
        description="A detailed developer tooling video that should be relevant.",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_video_ai_processing",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    fake_llm = SimpleNamespace(
        is_configured=lambda: True,
        get_provider=lambda: "fake",
        classify_blips_tech_relevance=lambda **kwargs: SimpleNamespace(
            is_blips_tech_relevant="yes",
            confidence=0.96,
            reason="Developer tooling video.",
        ),
        summarize_video=lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("Empty summary returned from LLM for tech-relevant video")
        ),
    )
    monkeypatch.setattr(content_ai_service, "invalidate_tiered_feed_cache", lambda *a, **k: None)
    monkeypatch.setattr(content_ai_service, "refresh_cached_playlist_items", lambda *a, **k: {})

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
    )
    db.commit()

    refreshed_item = db.get(ContentItem, item.id)
    assert result["changed"] is True
    assert refreshed_item.ai_processed is False
    assert refreshed_item.summary.startswith("__blips_video_summary_retry__:v1:1:")
    assert refreshed_item.readiness_status == "PENDING"
    assert refreshed_item.readiness_reason == "video_summary_retry"
    assert refreshed_item.tech_relevance_reason.startswith("video_summary_failure:")


def test_process_content_ai_summary_request_terminal_marks_repeated_video_summary_failure(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Example Channel",
        source_url="https://example.com/watch?v=terminal",
        video_url="https://example.com/watch?v=terminal",
        published_at=_recent_dt(hours_ago=1),
        title="Tech video with repeated empty LLM summaries",
        description="A detailed developer tooling video that should be relevant.",
        summary=f"__blips_video_summary_retry__:v1:2:{_recent_dt(minutes_ago=30).isoformat()}",
        ai_processed=False,
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="video_summary_retry",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    fake_llm = SimpleNamespace(
        is_configured=lambda: True,
        get_provider=lambda: "fake",
        classify_blips_tech_relevance=lambda **kwargs: SimpleNamespace(
            is_blips_tech_relevant="yes",
            confidence=0.96,
            reason="Developer tooling video.",
        ),
        summarize_video=lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("Empty summary returned from LLM for tech-relevant video")
        ),
    )
    monkeypatch.setattr(content_ai_service, "invalidate_tiered_feed_cache", lambda *a, **k: None)
    monkeypatch.setattr(content_ai_service, "refresh_cached_playlist_items", lambda *a, **k: {})

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
    )
    db.commit()

    refreshed_item = db.get(ContentItem, item.id)
    assert result["changed"] is True
    assert refreshed_item.ai_processed is True
    assert refreshed_item.summary.startswith("__blips_video_summary_retry__:v1:3:")
    assert refreshed_item.readiness_status == "PENDING"
    assert refreshed_item.readiness_reason == "video_summary_failed"


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
    fake_llm.classify_audience_lane.return_value = SimpleNamespace(
        lane="GENERAL_PUBLIC",
        confidence=0.91,
        reason="Major consumer product launch.",
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
    monkeypatch.setattr(
        content_ai_service,
        "ArticleHydrationService",
        lambda *args, **kwargs: fake_hydrator,
    )

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
    )

    assert result["changed"] is True
    assert len(mark_calls) == 1
    assert mark_calls[0]["tech_relevance"] == "yes"
    assert mark_calls[0]["tech_relevance_confidence"] == 0.97
    assert (
        mark_calls[0]["tech_relevance_reason"]
        == "Directly about a major tech company product launch."
    )

    db.commit()
    refreshed = db.get(ContentItem, item.id)
    assert refreshed.tech_relevance == "yes"
    assert refreshed.tech_relevance_confidence == 0.97


def test_reject_terminal_unskimmable_article_clears_promotion_score():
    """Fix 1 — promotion_score must be cleared on terminal rejection so stale scores
    from pre-rejection scoring runs cannot surface through the API."""
    from app.services.article_unskimmable_service import reject_terminal_unskimmable_article

    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="CNET",
        source_url="https://cnet.com/article/123",
        canonical_url="https://cnet.com/article/123",
        published_at=_recent_dt(hours_ago=3),
        title="Yellowstone sequel Marshals release date",
        description="Luke Grimes leads the Yellowstone sequel.",
        curation_status=ContentStatus.PROMOTED,
        promotion_score=0.4313,
        created_at=_recent_dt(hours_ago=2),
        updated_at=_recent_dt(hours_ago=2),
    )
    db.add(item)
    db.commit()

    result = reject_terminal_unskimmable_article(db, item, reason="max_unskimmable_attempts")
    db.commit()

    refreshed = db.get(ContentItem, item.id)
    assert result is True
    assert refreshed.promotion_score is None
    assert refreshed.is_suppressed is True
    assert "article_unskimmable_terminal" in (refreshed.tech_relevance_reason or "")


def test_process_article_summary_fallback_classifier_rejects_non_tech_when_body_too_short(
    monkeypatch,
):
    """Fix 2 — when body is below the summary threshold, the tech classifier should
    run on title + description; articles classified 'no' with confidence >= 0.50
    must be terminally rejected with reason 'llm_non_tech_article'."""
    from unittest.mock import MagicMock

    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="CNET",
        source_url="https://cnet.com/article/456",
        canonical_url="https://cnet.com/article/456",
        published_at=_recent_dt(hours_ago=2),
        title="Yellowstone sequel Marshals release date and full schedule",
        # Description is short — well under the 120-word body threshold
        description="Luke Grimes leads the Yellowstone sequel.",
        content_text="Luke Grimes leads the Yellowstone sequel.",
        image_url="https://cdn.example.com/hero.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        promotion_score=0.4313,
        ingestion_day=date.today(),
        created_at=_recent_dt(hours_ago=1),
        updated_at=_recent_dt(hours_ago=1),
    )
    db.add(item)
    db.add(
        IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=10,
            inserted=5,
            reserved=0,
            seen=5,
            suppressed=0,
            attempts=5,
        )
    )
    db.commit()

    fake_llm = MagicMock()
    fake_llm.is_configured.return_value = True
    fake_llm.get_provider.return_value = "fake"
    fake_llm.classify_blips_tech_relevance.return_value = SimpleNamespace(
        is_blips_tech_relevant="no",
        confidence=0.95,
        reason="TV entertainment article with no tech angle.",
    )

    fake_hydrator = MagicMock()
    fake_hydrator.refresh_http_metadata = MagicMock(return_value=None)
    fake_hydrator.refresh_canonical_url = MagicMock(return_value=None)
    fake_hydrator.refresh_image = MagicMock(return_value=None)

    result = content_ai_service.process_content_ai_summary_request(
        db,
        content_id=item.id,
        llm_client=fake_llm,
        article_hydrator=fake_hydrator,
    )
    db.commit()

    refreshed = db.get(ContentItem, item.id)
    assert result["changed"] is True
    # Article should be terminally rejected, not just deferred
    assert refreshed.is_suppressed is True
    assert refreshed.curation_status == ContentStatus.CANDIDATE
    # Promotion score must be cleared
    assert refreshed.promotion_score is None
    # Rejection reason must identify the llm classifier path
    assert "llm_non_tech_article" in (refreshed.tech_relevance_reason or "")
    assert refreshed.tech_relevance == "no"
    assert refreshed.tech_relevance_confidence == 0.95
    assert refreshed.promotion_reason == "Rejected automatically: non-tech article"
    # The classifier was called with the title + description fallback, not body text
    fake_llm.classify_blips_tech_relevance.assert_called_once()
    call_kwargs = fake_llm.classify_blips_tech_relevance.call_args.kwargs
    assert call_kwargs["title"] == item.title
    assert "Luke Grimes" in call_kwargs["summary"]
