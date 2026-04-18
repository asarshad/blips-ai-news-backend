from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.ingestion.checkpoint_worker import _queue_followup_events_for_inserted_ids
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import content_promotion_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _recent_dt(*, hours_ago: int = 0, minutes_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(hours=hours_ago, minutes=minutes_ago)


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_queue_content_promotion_request_dedupes_pending_rows():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/candidate",
        canonical_url="https://example.com/candidate",
        published_at=_recent_dt(hours_ago=1),
        title="Queued candidate",
        curation_status=ContentStatus.CANDIDATE,
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    first = content_promotion_service.queue_content_promotion_request(db, item)
    second = content_promotion_service.queue_content_promotion_request(db, item)
    db.commit()

    rows = db.query(ContentEventOutbox).all()

    assert first is not None
    assert second is None
    assert len(rows) == 1
    assert rows[0].event_type == content_promotion_service.CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE


def test_process_content_promotion_request_skips_non_candidate_item():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/promoted",
        canonical_url="https://example.com/promoted",
        published_at=_recent_dt(hours_ago=1),
        title="Already promoted",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    result = content_promotion_service.process_content_promotion_request(db, content_id=item.id)

    assert result["skipped"] is True
    assert result["reason"] == "not_candidate_or_suppressed"


def test_process_content_promotion_request_runs_type_scoped_promotion(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="YouTube",
        source_url="https://www.youtube.com/watch?v=test123",
        canonical_url="https://www.youtube.com/watch?v=test123",
        published_at=_recent_dt(hours_ago=1),
        title="Candidate video",
        curation_status=ContentStatus.CANDIDATE,
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    calls = []

    class _FakePromotionResult:
        promoted_ids = [item.id]
        errors = []

    class _FakePromotionService:
        def __init__(self, _db):
            self._db = _db

        def run_promotion_job(self, *, content_types=None):
            calls.append(content_types)
            target = self._db.get(ContentItem, item.id)
            target.curation_status = ContentStatus.PROMOTED
            target.promotion_score = 0.84
            target.readiness_status = "PENDING"
            return _FakePromotionResult()

    monkeypatch.setattr(content_promotion_service, "PromotionService", _FakePromotionService)

    result = content_promotion_service.process_content_promotion_request(db, content_id=item.id)

    refreshed = db.get(ContentItem, item.id)

    assert calls == [(ContentType.VIDEO,)]
    assert result["changed"] is True
    assert result["promoted"] is True
    assert refreshed.curation_status == ContentStatus.PROMOTED


def test_checkpoint_followup_events_queue_promotion_for_candidates(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/candidate",
        canonical_url="https://example.com/candidate",
        published_at=_recent_dt(hours_ago=1),
        title="Candidate article",
        curation_status=ContentStatus.CANDIDATE,
        readiness_status="PENDING",
        readiness_reason="awaiting_promotion",
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        "app.ingestion.checkpoint_worker.feature_flags.is_enabled",
        lambda feature: feature == "event_driven_promotion",
    )

    _queue_followup_events_for_inserted_ids(db, inserted_ids=[item.id])
    db.commit()

    rows = db.query(ContentEventOutbox).all()

    assert len(rows) == 1
    assert rows[0].event_type == content_promotion_service.CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE
    assert rows[0].content_item_id == item.id
