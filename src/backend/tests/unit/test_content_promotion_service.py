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


def test_queue_content_promotion_request_applies_initial_delay(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/delayed-candidate",
        canonical_url="https://example.com/delayed-candidate",
        published_at=_recent_dt(hours_ago=1),
        title="Delayed candidate",
        curation_status=ContentStatus.CANDIDATE,
        created_at=_recent_dt(minutes_ago=50),
        updated_at=_recent_dt(minutes_ago=50),
    )
    db.add(item)
    db.commit()

    queued_at = _recent_dt(minutes_ago=5)
    monkeypatch.setenv("CONTENT_PROMOTION_QUEUE_DELAY_SECONDS", "90")

    event = content_promotion_service.queue_content_promotion_request(db, item, now=queued_at)

    assert event is not None
    assert event.available_at == queued_at + timedelta(seconds=90)


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


def test_process_content_promotion_request_runs_targeted_promotion(monkeypatch):
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

    class _FakePromotionService:
        def __init__(self, _db):
            self._db = _db

        def evaluate_candidate_item(self, target):
            calls.append(target.id)
            target = self._db.get(ContentItem, item.id)
            target.curation_status = ContentStatus.PROMOTED
            target.promotion_score = 0.84
            target.readiness_status = "PENDING"
            return {
                "promoted": True,
                "candidate_rank": 1,
                "candidate_count": 1,
                "reason": "promoted",
            }

    monkeypatch.setattr(content_promotion_service, "PromotionService", _FakePromotionService)

    result = content_promotion_service.process_content_promotion_request(db, content_id=item.id)

    refreshed = db.get(ContentItem, item.id)

    assert calls == [item.id]
    assert result["changed"] is True
    assert result["promoted"] is True
    assert refreshed.curation_status == ContentStatus.PROMOTED


def test_checkpoint_followup_events_always_queue_promotion_for_candidates():
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

    _queue_followup_events_for_inserted_ids(db, inserted_ids=[item.id])
    db.commit()

    rows = db.query(ContentEventOutbox).all()
    event_types = {row.event_type for row in rows}

    assert content_promotion_service.CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE in event_types


def test_classify_promotion_block_blocks_non_tech_article_at_confidence_threshold():
    from app.services.promotion_service import classify_promotion_block

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Reuters",
        source_url="https://reuters.com/world/us-election",
        published_at=_recent_dt(hours_ago=1),
        title="US election results 2026",
        tech_relevance="no",
        tech_relevance_confidence=0.91,
        tech_relevance_reason="General politics with no meaningful tech angle.",
        curation_status=ContentStatus.PROMOTED,
    )

    reason = classify_promotion_block(
        item,
        ContentType.ARTICLE,
        story_topic_counts={},
        story_entity_counts={},
    )

    assert reason == "llm_non_tech_article"


def test_classify_promotion_block_does_not_block_article_with_null_tech_relevance():
    from app.services.promotion_service import classify_promotion_block

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="TechCrunch",
        source_url="https://techcrunch.com/story",
        published_at=_recent_dt(hours_ago=1),
        title="Apple unveils new chip",
        tech_relevance=None,
        tech_relevance_confidence=None,
        curation_status=ContentStatus.PROMOTED,
    )

    reason = classify_promotion_block(
        item,
        ContentType.ARTICLE,
        story_topic_counts={},
        story_entity_counts={},
    )

    assert reason is None


def test_classify_promotion_block_does_not_block_article_below_confidence_floor():
    from app.services.promotion_service import classify_promotion_block

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Reuters",
        source_url="https://reuters.com/world/story",
        published_at=_recent_dt(hours_ago=1),
        title="Some borderline story",
        tech_relevance="no",
        tech_relevance_confidence=0.45,
        tech_relevance_reason="Weak non-tech signal.",
        curation_status=ContentStatus.PROMOTED,
    )

    reason = classify_promotion_block(
        item,
        ContentType.ARTICLE,
        story_topic_counts={},
        story_entity_counts={},
    )

    assert reason is None
