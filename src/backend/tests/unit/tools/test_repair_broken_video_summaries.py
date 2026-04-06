from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from scripts import repair_broken_video_summaries


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_summary_needs_repair_detects_classifier_blob():
    assert (
        repair_broken_video_summaries.summary_needs_repair(
            '{"tech_relevance":"primary","confidence":0.82,"is_mixed_roundup":false,"summary":"Bad"}'
        )
        is True
    )
    assert repair_broken_video_summaries.summary_needs_repair("Plain summary text.") is False


def test_repair_broken_video_summaries_regenerates_and_refreshes_caches(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        id=57,
        type=ContentType.VIDEO,
        source="Max Tech",
        source_url="https://www.youtube.com/watch?v=mac57",
        video_url="https://www.youtube.com/watch?v=mac57",
        title="Apple's 2026 Macs have LEAKED!",
        description="Rumored Macs, specs, and upgrades.",
        summary='{"tech_relevance":"primary","confidence":0.66,"is_mixed_roundup":false,"summary":"Broken"}',
        ai_processed=True,
        curation_status=ContentStatus.PROMOTED,
        published_at=datetime.utcnow() - timedelta(hours=2),
        created_at=datetime.utcnow() - timedelta(hours=1, minutes=55),
        updated_at=datetime.utcnow() - timedelta(hours=1, minutes=55),
    )
    db.add(item)
    db.commit()

    llm_client = SimpleNamespace(
        is_configured=lambda: True,
        get_provider=lambda: "openai",
        summarize_video=lambda _title, _text: SimpleNamespace(
            summary=(
                "The video previews several rumored 2026 Mac models, including claimed desktop and "
                "laptop updates, expected chip changes, and the practical performance implications "
                "buyers and developers should watch for if Apple ships the lineup as described. "
                "It also compares the rumored desktop roadmap, expected laptop refresh timing, "
                "possible performance gains, and upgrade decisions that matter for people planning "
                "hardware purchases or development work around Apple's next chip cycle."
            ),
            conversation_starters={"starters": ["Which rumored Mac matters most here?"]},
            tech_relevance="primary",
            tech_relevance_confidence=0.9,
            tech_relevance_reason="This is directly about Apple hardware.",
            is_mixed_roundup=False,
        ),
    )
    cache_refresh_calls = []
    invalidation_calls = []

    monkeypatch.setattr(repair_broken_video_summaries, "SessionLocal", lambda: db)
    monkeypatch.setattr(repair_broken_video_summaries, "LLMClient", lambda: llm_client)
    monkeypatch.setattr(
        repair_broken_video_summaries,
        "sync_content_readiness",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        repair_broken_video_summaries,
        "refresh_cached_playlist_items",
        lambda _db, *, content_ids: cache_refresh_calls.append(content_ids) or {},
    )
    monkeypatch.setattr(
        repair_broken_video_summaries,
        "invalidate_tiered_feed_cache",
        lambda: invalidation_calls.append("invalidate"),
    )

    stats = repair_broken_video_summaries.repair_broken_video_summaries(hours_back=72, limit=50)
    repaired = db.get(ContentItem, 57)

    assert stats.matched == 1
    assert stats.regenerated == 1
    assert stats.excluded == 0
    assert repaired.ai_processed is True
    assert repaired.summary.startswith("The video previews several rumored 2026 Mac models")
    assert invalidation_calls == ["invalidate"]
    assert cache_refresh_calls == [[57]]
