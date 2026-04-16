from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.api.routes import metrics as metrics_route
from app.api.routes.metrics import _curation_mix_bucket
from app.models.content import ContentItem
from app.models.ingestion_progress import IngestionProgress
from app.models.source import SourceDailyStat
from app.models.video_source import VideoSourceProfile
from app.services import article_supply_metrics_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


@compiles(PgEnum, "sqlite")
def _compile_enum_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_curation_mix_bucket_treats_search_and_trending_as_discovery():
    assert _curation_mix_bucket("yt_search") == "discovery"
    assert _curation_mix_bucket("yt_search:ai-models") == "discovery"
    assert _curation_mix_bucket("yt_trending") == "discovery"
    assert _curation_mix_bucket("discovery_seed") == "discovery"
    assert _curation_mix_bucket("yt_curated") == "curated"


def test_get_source_health_metrics_counts_source_actions_by_channel_id(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    for table in (
        ContentItem.__table__,
        IngestionProgress.__table__,
        SourceDailyStat.__table__,
        VideoSourceProfile.__table__,
    ):
        table.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    frozen_day = date(2026, 3, 20)
    frozen_now = datetime(2026, 3, 20, 12, 0, 0)

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return frozen_day

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

    monkeypatch.setattr(metrics_route, "date", _FrozenDate)
    monkeypatch.setattr(metrics_route, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        metrics_route.extraction_metrics,
        "get_counters",
        lambda: {"content_filtered_language_total": 3},
    )

    db.add(
        IngestionProgress(
            day_utc=frozen_day,
            source_type="rss",
            feed_name="Tech Feed",
            target=10,
            items_ingested=8,
            items_attempted=10,
            status="complete",
            retry_count=0,
            updated_at=frozen_now,
        )
    )
    db.add(SourceDailyStat(day=frozen_day, source="rss", inserted=8, suppressed=1))
    db.add(
        VideoSourceProfile(
            channel_id="channel-1",
            channel_name="Channel One",
            role="news",
            content_format="long_form",
            quality_tier="standard",
            status="rotation",
            enabled=True,
            score_7d=0.51,
            promotion_rate_7d=0.31,
            status_changed_at=frozen_now,
        )
    )
    db.commit()

    payload = metrics_route.get_source_health_metrics(db)

    assert "error" not in payload
    assert payload["source_promotion_demotion_actions"] == {"rotation": 1}
    assert payload["language_filtered_count"] == 3


def test_get_article_supply_metrics_returns_daily_and_source_breakdown(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    for table in (
        ContentItem.__table__,
        IngestionProgress.__table__,
        SourceDailyStat.__table__,
        VideoSourceProfile.__table__,
    ):
        table.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    frozen_now = datetime(2026, 4, 15, 18, 0, 0)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    day = date(2026, 4, 15)
    monkeypatch.setattr(article_supply_metrics_service, "datetime", _FrozenDatetime)
    db.add_all(
        [
            ContentItem(
                type="ARTICLE",
                source="Tech Feed",
                source_url="https://example.com/a",
                published_at=datetime(2026, 4, 15, 12, 0, 0),
                title="Ready promoted",
                curation_status="PROMOTED",
                readiness_status="READY",
                is_suppressed=False,
            ),
            ContentItem(
                type="ARTICLE",
                source="Tech Feed",
                source_url="https://example.com/b",
                published_at=datetime(2026, 4, 15, 13, 0, 0),
                title="Pending promoted",
                curation_status="PROMOTED",
                readiness_status="PENDING",
                readiness_reason="missing_article_image",
                is_suppressed=False,
            ),
            ContentItem(
                type="ARTICLE",
                source="Tech Feed",
                source_url="https://example.com/c",
                published_at=datetime(2026, 4, 15, 14, 0, 0),
                title="Candidate article",
                curation_status="CANDIDATE",
                readiness_status="PENDING",
                readiness_reason="awaiting_promotion",
                is_suppressed=False,
            ),
            IngestionProgress(
                day_utc=day,
                source_type="rss",
                feed_name="Tech Feed",
                target=5,
                items_ingested=3,
                items_attempted=7,
                status="running",
                retry_count=1,
                updated_at=datetime(2026, 4, 15, 15, 0, 0),
            ),
        ]
    )
    db.commit()

    payload = metrics_route.get_article_supply_metrics(days=1, db=db)

    assert payload["days"] == 1
    assert payload["daily"][0]["day"] == "2026-04-15"
    assert payload["daily"][0]["total_published"] == 3
    assert payload["daily"][0]["candidate_count"] == 1
    assert payload["daily"][0]["promoted_count"] == 2
    assert payload["daily"][0]["ready_count"] == 1
    assert payload["daily"][0]["pending_count"] == 1
    assert payload["daily"][0]["pending_by_reason"] == {"missing_article_image": 1}
    assert payload["sources"][0]["source"] == "Tech Feed"
    assert payload["sources"][0]["attempted"] == 7
    assert payload["sources"][0]["inserted"] == 3
    assert payload["sources"][0]["promoted"] == 2
    assert payload["sources"][0]["ready"] == 1
