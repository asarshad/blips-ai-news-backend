from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.repositories.content_repo import ContentItemRepository


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _make_query_chain(session: MagicMock) -> MagicMock:
    query = MagicMock()
    session.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    return query


def test_get_articles_with_short_summaries_filters_out_long_and_blank_summaries():
    session = MagicMock()
    repo = ContentItemRepository(session)
    query = _make_query_chain(session)

    short_item = SimpleNamespace(summary="One two three four")
    long_item = SimpleNamespace(summary=" ".join(f"word{i}" for i in range(12)))
    blank_item = SimpleNamespace(summary="   ")
    another_short = SimpleNamespace(summary="Only five words here now")
    query.all.return_value = [short_item, long_item, blank_item, another_short]

    result = repo.get_articles_with_short_summaries(limit=2, max_words=10)

    assert result == [short_item, another_short]
    query.limit.assert_called_once_with(10)


def test_get_articles_with_short_summaries_respects_requested_limit():
    session = MagicMock()
    repo = ContentItemRepository(session)
    query = _make_query_chain(session)

    query.all.return_value = [
        SimpleNamespace(summary="short summary words"),
        SimpleNamespace(summary="another short summary"),
        SimpleNamespace(summary="third short summary"),
    ]

    result = repo.get_articles_with_short_summaries(limit=2, max_words=10)

    assert len(result) == 2


def test_get_items_for_playlist_blocks_stale_ready_videos_without_ai_summary():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    repo = ContentItemRepository(db)
    now = datetime.utcnow()

    stale_video = ContentItem(
        id=1,
        type=ContentType.VIDEO,
        source="CNET",
        source_url="https://www.youtube.com/watch?v=stale123",
        title="Stale promoted video",
        summary="Watch all the drama and excitement...",
        video_url="https://www.youtube.com/watch?v=stale123",
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="video_ready",
        ai_processed=False,
        published_at=now - timedelta(hours=3),
        created_at=now - timedelta(hours=2, minutes=55),
    )
    valid_video = ContentItem(
        id=2,
        type=ContentType.VIDEO,
        source="The Verge",
        source_url="https://www.youtube.com/watch?v=valid123",
        title="Valid promoted video",
        summary=(
            "A proper AI summary with enough concrete detail to satisfy the delivery gate. "
            "It explains the product announcement, the developer tooling changes, the rollout "
            "timing, and the practical impact on teams deciding whether to adopt the platform "
            "this year. It also includes enough factual context to meet the minimum summary "
            "length required for promoted videos on the surface."
        ),
        video_url="https://www.youtube.com/watch?v=valid123",
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="video_ready",
        ai_processed=True,
        published_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=1, minutes=55),
    )
    db.add_all([stale_video, valid_video])
    db.commit()

    results = repo.get_items_for_playlist(ContentType.VIDEO, hours_back=168, limit=10)

    assert [item.id for item in results] == [2]


def test_get_article_supply_counts_on_ingestion_day_uses_ready_supply_not_raw_created_rows():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    repo = ContentItemRepository(db)
    now = datetime.utcnow()

    ready_article = ContentItem(
        id=10,
        type=ContentType.ARTICLE,
        source="The Verge",
        source_url="https://example.com/ready",
        title="Ready article",
        summary="A complete article summary with enough detail for delivery.",
        image_url="https://example.com/image.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="article_ready",
        ai_processed=True,
        published_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
        ingestion_day=now.date(),
    )
    pending_article = ContentItem(
        id=11,
        type=ContentType.ARTICLE,
        source="Ars Technica",
        source_url="https://example.com/pending",
        title="Pending article",
        summary="Still waiting on a verified image.",
        image_url=None,
        article_image_status="MISSING",
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.PENDING.value,
        readiness_reason="missing_article_image",
        ai_processed=True,
        published_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
        ingestion_day=now.date(),
    )
    older_ready_article = ContentItem(
        id=12,
        type=ContentType.ARTICLE,
        source="Wired",
        source_url="https://example.com/older",
        title="Older ready article",
        summary="Older article outside the current ingestion day window.",
        image_url="https://example.com/older.jpg",
        article_image_status="VERIFIED",
        curation_status=ContentStatus.PROMOTED,
        readiness_status=ContentReadinessStatus.READY.value,
        readiness_reason="article_ready",
        ai_processed=True,
        published_at=now - timedelta(days=1, hours=1),
        created_at=now - timedelta(days=1, hours=1),
    )
    db.add_all([ready_article, pending_article, older_ready_article])
    db.commit()

    counts = repo.get_article_supply_counts_on_ingestion_day(now.date())

    assert counts == {"promoted": 2, "ready": 1}
