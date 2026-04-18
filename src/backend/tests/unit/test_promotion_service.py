from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.services.promotion_service import (
    _DEFAULT_CONFIG,
    _REEL_CONFIG,
    _VIDEO_CONFIG,
    PromotionService,
    compute_story_keyword_signal,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_video_and_reel_configs_use_longer_half_lives():
    assert _VIDEO_CONFIG.window_hours == 168
    assert _REEL_CONFIG.window_hours == 168
    assert _DEFAULT_CONFIG.min_score == 0.25
    assert _VIDEO_CONFIG.recency_half_life_hours == 72.0
    assert _REEL_CONFIG.recency_half_life_hours == 48.0
    assert _VIDEO_CONFIG.min_score == 0.25
    assert _REEL_CONFIG.min_score == 0.22
    assert _VIDEO_CONFIG.w_story > 0.0
    assert _REEL_CONFIG.w_story > 0.0


def test_story_keyword_signal_recognizes_launch_framing():
    signal = compute_story_keyword_signal(
        "Apple launch keynote first look",
        "Hands on review with benchmark impressions.",
    )

    assert signal > 0.4


def test_reels_use_lower_auto_promote_threshold_by_default():
    service = PromotionService(MagicMock())

    assert service._min_promotion_score(ContentType.REEL, _REEL_CONFIG) == 0.22
    assert service._min_promotion_score(ContentType.VIDEO, _VIDEO_CONFIG) == _VIDEO_CONFIG.min_score


def test_curated_high_score_reels_can_fall_back_to_single_promotion_slot():
    service = PromotionService(MagicMock())
    item = SimpleNamespace(
        acquisition_lane="curated",
        promotion_score=0.4304,
        source="TechCrunch",
    )
    source_profile = SimpleNamespace(
        daily_reel_cap=0,
        allow_curated=True,
        channel_name="TechCrunch",
    )

    cap = service._surface_channel_cap(
        ContentType.REEL,
        item=item,
        channel_config=None,
        source_profile=source_profile,
    )

    assert cap == 1


def test_broad_news_reels_do_not_use_zero_cap_fallback():
    service = PromotionService(MagicMock())
    item = SimpleNamespace(
        acquisition_lane="curated",
        promotion_score=0.4304,
        source="Reuters",
    )
    source_profile = SimpleNamespace(
        daily_reel_cap=0,
        allow_curated=True,
        channel_name="Reuters",
    )

    cap = service._surface_channel_cap(
        ContentType.REEL,
        item=item,
        channel_config=None,
        source_profile=source_profile,
    )

    assert cap == 0


def test_rescore_promoted_preserves_editorially_approved_items(monkeypatch):
    item = SimpleNamespace(
        id=1,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        published_at=datetime.utcnow() - timedelta(hours=1),
        channel_id="channel-1",
        source="Test Source",
        manual_added=False,
        last_modified_by="admin",
        promotion_score=None,
        promotion_reason=None,
    )
    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = [item]
    db = MagicMock()
    db.query.return_value = query
    service = PromotionService(db)

    monkeypatch.setattr(
        "app.services.promotion_service.apply_content_policy",
        lambda query, **_kwargs: query,
    )
    monkeypatch.setattr(
        "app.services.promotion_service.score_candidate",
        lambda *_args, **_kwargs: 0.12,
    )
    monkeypatch.setattr(
        "app.services.promotion_service.classify_promotion_block",
        lambda *_args, **_kwargs: "off_topic_news_video",
    )
    monkeypatch.setattr(service, "_promotion_reason", lambda *_args, **_kwargs: "base")
    monkeypatch.setattr(service, "_get_source_profiles", lambda *_args, **_kwargs: {})

    rescored = service._rescore_promoted(ContentType.VIDEO, {}, _VIDEO_CONFIG)

    assert rescored == 1
    assert item.curation_status == ContentStatus.PROMOTED
    assert item.promotion_score == 0.12
    assert (
        item.promotion_reason
        == "base|override_block=off_topic_news_video|preserved=editorial_override"
    )


def test_rescore_promoted_demotes_blocked_non_editorial_items(monkeypatch):
    item = SimpleNamespace(
        id=2,
        type=ContentType.VIDEO,
        curation_status=ContentStatus.PROMOTED,
        published_at=datetime.utcnow() - timedelta(hours=1),
        channel_id="channel-2",
        source="Test Source",
        manual_added=False,
        last_modified_by=None,
        promotion_score=None,
        promotion_reason=None,
    )
    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = [item]
    db = MagicMock()
    db.query.return_value = query
    service = PromotionService(db)

    monkeypatch.setattr(
        "app.services.promotion_service.apply_content_policy",
        lambda query, **_kwargs: query,
    )
    monkeypatch.setattr(
        "app.services.promotion_service.score_candidate",
        lambda *_args, **_kwargs: 0.12,
    )
    monkeypatch.setattr(
        "app.services.promotion_service.classify_promotion_block",
        lambda *_args, **_kwargs: "off_topic_news_video",
    )
    monkeypatch.setattr(service, "_promotion_reason", lambda *_args, **_kwargs: "base")
    monkeypatch.setattr(service, "_get_source_profiles", lambda *_args, **_kwargs: {})

    rescored = service._rescore_promoted(ContentType.VIDEO, {}, _VIDEO_CONFIG)

    assert rescored == 1
    assert item.curation_status == ContentStatus.CANDIDATE
    assert item.promotion_score == 0.12
    assert item.promotion_reason == "base|blocked=off_topic_news_video"


def test_recent_promotion_context_counts_only_visible_ready_items(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    now = datetime.utcnow()

    ready_item = ContentItem(
        type=ContentType.VIDEO,
        source="Ready Source",
        source_url="https://example.com/watch/ready",
        canonical_url="https://example.com/watch/ready",
        channel_id="ready-channel",
        published_at=now,
        title="Ready item",
        topics=["ai"],
        entities=["openai"],
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="video_ready",
        promotion_reason="curated|core|fit=0.55|vph=0.0|story=0.50",
        is_suppressed=False,
        created_at=now,
        updated_at=now,
        ready_at=now,
        readiness_updated_at=now,
    )
    blocked_item = ContentItem(
        type=ContentType.VIDEO,
        source="Blocked Source",
        source_url="https://example.com/watch/blocked",
        canonical_url="https://example.com/watch/blocked",
        channel_id="blocked-channel",
        published_at=now,
        title="Blocked item",
        topics=["security"],
        entities=["google"],
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="video_ready",
        promotion_reason="curated|core|fit=0.55|vph=0.0|story=0.50|blocked=weak_tech_signal_video",
        is_suppressed=False,
        created_at=now,
        updated_at=now,
        ready_at=now,
        readiness_updated_at=now,
    )
    pending_item = ContentItem(
        type=ContentType.VIDEO,
        source="Pending Source",
        source_url="https://example.com/watch/pending",
        canonical_url="https://example.com/watch/pending",
        channel_id="pending-channel",
        published_at=now,
        title="Pending item",
        topics=["privacy"],
        entities=["meta"],
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.PENDING.value,
        readiness_reason="awaiting_promotion",
        promotion_reason="curated|core|fit=0.55|vph=0.0|story=0.50",
        is_suppressed=False,
        created_at=now,
        updated_at=now,
        readiness_updated_at=now,
    )
    db.add_all([ready_item, blocked_item, pending_item])
    db.commit()

    monkeypatch.setattr(
        "app.services.promotion_service.apply_content_policy",
        lambda query, **_kwargs: query,
    )

    service = PromotionService(db)

    assert service._get_recent_promoted_topic_counts(ContentType.VIDEO) == {"ai": 1}
    assert service._get_recent_promoted_channel_counts(ContentType.VIDEO) == {"ready-channel": 1}

    topic_counts, entity_counts = service._get_recent_story_context()
    assert topic_counts == {"ai": 1}
    assert entity_counts == {"openai": 1}
