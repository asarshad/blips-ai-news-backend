from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentReadinessStatus, ContentStatus, ContentType
from app.services.content_readiness import (
    CONTENT_READY_EVENT_TYPE,
    CONTENT_UNREADY_EVENT_TYPE,
    build_content_ready_event_payload,
    build_content_unready_event_payload,
    evaluate_content_readiness,
    sync_content_readiness,
)


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


def test_video_is_ready_without_summary_when_core_fields_exist():
    video = SimpleNamespace(
        id=2,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Fresh promoted video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
    )

    decision = evaluate_content_readiness(video)

    assert decision.status == ContentReadinessStatus.READY
    assert decision.reason == "video_ready"
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
