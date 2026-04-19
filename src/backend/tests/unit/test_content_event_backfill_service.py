from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import content_event_backfill_service as backfill_module
from app.services.content_event_backfill_service import enqueue_pending_content_events


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_enqueue_pending_content_events_queues_expected_work(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    candidate = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/candidate",
        canonical_url="https://example.com/candidate",
        published_at=datetime.utcnow(),
        title="Candidate article",
        curation_status=ContentStatus.CANDIDATE,
        readiness_status="PENDING",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    promoted_article = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/promoted",
        canonical_url="https://example.com/promoted",
        published_at=datetime.utcnow(),
        title="Promoted article",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_article_image_verification",
        article_image_status="PENDING",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    promoted_video = ContentItem(
        type=ContentType.VIDEO,
        source="Example",
        source_url="https://example.com/video",
        published_at=datetime.utcnow(),
        title="Promoted video",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_video_ai_processing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([candidate, promoted_article, promoted_video])
    db.commit()

    monkeypatch.setattr(
        backfill_module,
        "sync_content_readiness",
        lambda db, item: None,
    )

    promotion_ids = []
    ai_ids = []
    image_ids = []

    monkeypatch.setattr(
        backfill_module,
        "queue_content_promotion_request",
        lambda db, item: promotion_ids.append(item.id) or object()
        if item.curation_status == ContentStatus.CANDIDATE
        else None,
    )
    monkeypatch.setattr(
        backfill_module,
        "queue_content_ai_summary_request",
        lambda db, item: ai_ids.append(item.id) or object()
        if item.curation_status == ContentStatus.PROMOTED
        else None,
    )
    monkeypatch.setattr(
        backfill_module,
        "queue_article_image_verification_request",
        lambda db, item: image_ids.append(item.id) or object()
        if item.type == ContentType.ARTICLE and item.curation_status == ContentStatus.PROMOTED
        else None,
    )

    result = enqueue_pending_content_events(db, lookback_days=7, limit=10)

    assert result["job"] == "content_event_backfill"
    assert result["scanned"] == 3
    assert result["queued"] == {
        "promotion": 1,
        "ai_summary": 2,
        "article_image_verification": 1,
    }
    assert candidate.id in promotion_ids
    assert promoted_article.id in ai_ids and promoted_video.id in ai_ids
    assert image_ids == [promoted_article.id]


def test_enqueue_pending_content_events_respects_pending_only():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    ready_item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/ready",
        canonical_url="https://example.com/ready",
        published_at=datetime.utcnow(),
        title="Ready article",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="READY",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    pending_item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/pending",
        canonical_url="https://example.com/pending",
        published_at=datetime.utcnow(),
        title="Pending article",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([ready_item, pending_item])
    db.commit()

    result = enqueue_pending_content_events(db, lookback_days=7, limit=10, pending_only=True)

    assert result["scanned"] == 1
    assert result["by_type"] == {"ARTICLE": 1}
