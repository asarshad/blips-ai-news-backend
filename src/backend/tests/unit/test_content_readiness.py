from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.content import ContentReadinessStatus, ContentStatus, ContentType
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
from app.services.content_readiness import (
    CONTENT_READY_EVENT_TYPE,
    CONTENT_UNREADY_EVENT_TYPE,
    build_content_ready_event_payload,
    build_content_unready_event_payload,
    evaluate_content_readiness,
    ready_content_filter,
    sync_content_readiness,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_article_requires_ai_summary_to_be_ready():
    article = SimpleNamespace(
        id=1,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/article",
        canonical_url="https://example.com/article",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="VERIFIED",
        ai_processed=False,
        summary=None,
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "awaiting_ai_processing"
    assert decision.surfaces == ("articles",)


def test_article_unskimmable_retry_has_specific_reason():
    article = SimpleNamespace(
        id=12,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/article",
        canonical_url="https://example.com/article",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="VERIFIED",
        ai_processed=False,
        summary="__blips_article_retry__:v1:1:2026-04-20T12:00:00",
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "article_unskimmable_retry"


def test_non_tech_article_is_not_ready_even_with_summary_and_image():
    article = SimpleNamespace(
        id=24,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/local-politics",
        canonical_url="https://example.com/local-politics",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        summary="A complete summary that would otherwise satisfy article delivery readiness.",
        tech_relevance="no",
        tech_relevance_confidence=0.95,
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "article_non_tech"


def test_article_quality_gate_blocks_daily_puzzle_help():
    article = SimpleNamespace(
        id=25,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://www.cnet.com/tech/gaming/todays-nyt-connections-hints-and-answers",
        canonical_url="https://www.cnet.com/tech/gaming/todays-nyt-connections-hints-and-answers",
        title="Today's NYT Connections Hints, Answers for May 6",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        summary="Daily puzzle hints and answers.",
        tech_relevance="yes",
        tech_relevance_confidence=0.95,
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "article_non_news_puzzle_help"


def test_article_quality_gate_blocks_dnssec_debugger_tool_page():
    article = SimpleNamespace(
        id=26,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://dnssec-analyzer.verisignlabs.com/nic.de",
        canonical_url="https://dnssec-analyzer.verisignlabs.com/nic.de",
        title="DNSSEC Debugger - nic.de",
        image_url="https://dnssec-analyzer.verisignlabs.com/green.png",
        article_image_status="VERIFIED",
        ai_processed=True,
        summary="Debugger output for DNSSEC validation.",
        tech_relevance="yes",
        tech_relevance_confidence=0.95,
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "article_utility_tool_page"


def test_video_requires_ai_summary_to_be_ready():
    video = SimpleNamespace(
        id=2,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Fresh promoted video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        ai_processed=False,
        summary=None,
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "awaiting_video_ai_processing"
    assert decision.surfaces == ("videos",)


def test_video_summary_retry_has_specific_reason():
    video = SimpleNamespace(
        id=21,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Fresh promoted video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        ai_processed=False,
        summary="__blips_video_summary_retry__:v1:1:2026-04-20T12:00:00",
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "video_summary_retry"


def test_video_exhausted_summary_retry_has_terminal_reason():
    video = SimpleNamespace(
        id=22,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Fresh promoted video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        ai_processed=True,
        summary="__blips_video_summary_retry__:v1:3:2026-04-20T12:00:00",
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "video_summary_failed"


def test_non_tech_video_has_terminal_relevance_reason():
    video = SimpleNamespace(
        id=23,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="General protest video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        ai_processed=True,
        summary=None,
        tech_relevance="none",
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "video_non_tech"


def test_video_requires_non_empty_summary_after_ai_processing():
    video = SimpleNamespace(
        id=3,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Fresh promoted video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        ai_processed=True,
        summary="   ",
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "missing_video_summary"
    assert decision.surfaces == ("videos",)


def test_article_waits_for_image_verification_before_becoming_ready():
    article = SimpleNamespace(
        id=8,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/article",
        canonical_url="https://example.com/article",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="PENDING",
        ai_processed=True,
        summary="This article is summarized but still awaiting image verification.",
    )

    decision = evaluate_content_readiness(article)

    assert decision.status == ContentReadinessStatus.PENDING
    assert decision.reason == "awaiting_article_image_verification"


def test_ready_content_filter_excludes_ready_articles_with_blank_image_url():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    from app.models.content import ContentItem

    stale_ready = ContentItem(
        id=101,
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/stale-ready",
        canonical_url="https://example.com/stale-ready",
        title="Stale ready article",
        summary="A valid looking summary that should still be blocked.",
        image_url="   ",
        article_image_status="VERIFIED",
        ai_processed=True,
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        published_at=datetime(2026, 4, 20, 12, 0, 0),
    )
    healthy_ready = ContentItem(
        id=102,
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/healthy-ready",
        canonical_url="https://example.com/healthy-ready",
        title="Healthy ready article",
        summary="A valid summary for an article with a verified image.",
        image_url="https://cdn.example.com/healthy.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        published_at=datetime(2026, 4, 20, 12, 5, 0),
    )
    non_tech_ready = ContentItem(
        id=103,
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/non-tech",
        canonical_url="https://example.com/non-tech",
        title="General politics story",
        summary="A valid summary for a non-tech article.",
        image_url="https://cdn.example.com/non-tech.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        tech_relevance="no",
        tech_relevance_confidence=0.95,
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        published_at=datetime(2026, 4, 20, 12, 10, 0),
    )
    puzzle_ready = ContentItem(
        id=104,
        type=ContentType.ARTICLE,
        source="CNET",
        source_url="https://www.cnet.com/tech/gaming/todays-nyt-wordle-hints-answer-and-help",
        canonical_url="https://www.cnet.com/tech/gaming/todays-nyt-wordle-hints-answer-and-help",
        title="Today's NYT Wordle Hints, Answer and Help",
        summary="A valid summary for a puzzle help page.",
        image_url="https://cdn.example.com/puzzle.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        tech_relevance="yes",
        tech_relevance_confidence=0.95,
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        published_at=datetime(2026, 4, 20, 12, 15, 0),
    )
    manual_puzzle_ready = ContentItem(
        id=105,
        type=ContentType.ARTICLE,
        source="CNET",
        source_url="https://www.cnet.com/tech/gaming/todays-nyt-wordle-hints-answer-and-help-manual",
        canonical_url="https://www.cnet.com/tech/gaming/todays-nyt-wordle-hints-answer-and-help-manual",
        title="Today's NYT Wordle Hints, Answer and Help",
        summary="A manually-added puzzle item should only be delivered when explicitly curated.",
        image_url="https://cdn.example.com/manual-puzzle.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        manual_added=True,
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        published_at=datetime(2026, 4, 20, 12, 20, 0),
    )
    db.add_all([stale_ready, healthy_ready, non_tech_ready, puzzle_ready, manual_puzzle_ready])
    db.commit()

    rows = db.query(ContentItem).filter(ready_content_filter("articles")).all()

    assert [row.id for row in rows] == [102, 105]


def test_sync_content_readiness_enqueues_ready_event_once():
    now = datetime(2026, 3, 23, 18, 0, 0)
    db = MagicMock()
    article = SimpleNamespace(
        id=3,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/ready",
        canonical_url="https://example.com/ready",
        image_url="https://cdn.example.com/ready.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        summary="This article now has a real summary and is ready for clients.",
        readiness_status=ContentReadinessStatus.PENDING.value,
        readiness_reason="awaiting_ai_processing",
        ready_at=None,
        published_at=now,
        created_at=now,
    )

    result = sync_content_readiness(db, article, now=now)

    assert result.transitioned_to_ready is True
    assert result.transitioned_from_ready is False
    assert article.readiness_status == ContentReadinessStatus.READY.value
    assert article.ready_at == now
    queued_event = db.add.call_args[0][0]
    assert queued_event.event_type == CONTENT_READY_EVENT_TYPE
    assert queued_event.content_item_id == 3

    sync_content_readiness(db, article, now=now)
    assert db.add.call_count == 1


def test_sync_content_readiness_enqueues_article_image_verification_for_promoted_pending_article():
    now = datetime(2026, 3, 23, 18, 30, 0)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    article = SimpleNamespace(
        id=13,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/pending-image",
        canonical_url="https://example.com/pending-image",
        image_url="https://cdn.example.com/pending.jpg",
        article_image_status="PENDING",
        ai_processed=True,
        summary="Promoted article waiting on image verification.",
        readiness_status=ContentReadinessStatus.PENDING.value,
        readiness_reason="awaiting_article_image_verification",
        ready_at=None,
        published_at=now,
        created_at=now,
    )

    sync_content_readiness(db, article, now=now)

    queued_event = db.add.call_args[0][0]
    assert queued_event.event_type == ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
    assert queued_event.content_item_id == 13


def test_sync_content_readiness_does_not_enqueue_article_image_verification_for_candidate_article():
    now = datetime(2026, 3, 23, 18, 45, 0)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    article = SimpleNamespace(
        id=14,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.CANDIDATE,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/candidate-pending-image",
        canonical_url="https://example.com/candidate-pending-image",
        image_url="https://cdn.example.com/pending.jpg",
        article_image_status="PENDING",
        ai_processed=True,
        summary="Candidate article waiting on image verification.",
        readiness_status=ContentReadinessStatus.PENDING.value,
        readiness_reason="awaiting_article_image_verification",
        ready_at=None,
        published_at=now,
        created_at=now,
    )

    sync_content_readiness(db, article, now=now)

    db.add.assert_not_called()


def test_sync_content_readiness_enqueues_unready_event_on_ready_regression():
    now = datetime(2026, 3, 23, 19, 0, 0)
    db = MagicMock()
    article = SimpleNamespace(
        id=4,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/ready",
        canonical_url="https://example.com/ready",
        image_url="https://cdn.example.com/ready.jpg",
        article_image_status="VERIFIED",
        ai_processed=False,
        summary=None,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="article_ready",
        ready_at=now,
        published_at=now,
        created_at=now,
    )

    result = sync_content_readiness(db, article, now=now)

    assert result.transitioned_to_ready is False
    assert result.transitioned_from_ready is True
    assert article.readiness_status == ContentReadinessStatus.PENDING.value
    assert article.ready_at is None
    queued_event = db.add.call_args[0][0]
    assert queued_event.event_type == CONTENT_UNREADY_EVENT_TYPE
    assert queued_event.content_item_id == 4


def test_build_content_ready_event_payload_includes_delivery_metadata():
    now = datetime(2026, 3, 23, 18, 0, 0)
    video = SimpleNamespace(
        id=9,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Video ready",
        source_url="https://www.youtube.com/watch?v=abc123",
        video_url="https://www.youtube.com/watch?v=abc123",
        ai_processed=True,
        summary="A verified AI summary that is ready for client delivery.",
        published_at=now,
        created_at=now,
        ready_at=now,
    )

    payload = build_content_ready_event_payload(video)

    assert payload["content_id"] == 9
    assert payload["effective_type"] == ContentType.VIDEO.value
    assert payload["surfaces"] == ["videos"]
    assert payload["ready_at"] == now.isoformat()


def test_build_content_unready_event_payload_preserves_reason_metadata():
    now = datetime(2026, 3, 23, 18, 0, 0)
    article = SimpleNamespace(
        id=10,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/article",
        canonical_url="https://example.com/article",
        image_url="https://cdn.example.com/article.jpg",
        article_image_status="VERIFIED",
        ai_processed=False,
        summary=None,
        published_at=now,
        created_at=now,
        ready_at=None,
    )

    payload = build_content_unready_event_payload(article)

    assert payload["content_id"] == 10
    assert payload["effective_type"] == ContentType.ARTICLE.value
    assert payload["surfaces"] == ["articles"]
    assert payload["readiness_reason"] == "awaiting_ai_processing"


def test_build_content_unready_event_payload_uses_video_summary_reason():
    now = datetime(2026, 3, 23, 18, 0, 0)
    video = SimpleNamespace(
        id=11,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Video pending summary",
        source_url="https://www.youtube.com/watch?v=abc123",
        video_url="https://www.youtube.com/watch?v=abc123",
        ai_processed=True,
        summary=None,
        published_at=now,
        created_at=now,
        ready_at=None,
    )

    payload = build_content_unready_event_payload(video)

    assert payload["content_id"] == 11
    assert payload["effective_type"] == ContentType.VIDEO.value
    assert payload["surfaces"] == ["videos"]
    assert payload["readiness_reason"] == "missing_video_summary"
