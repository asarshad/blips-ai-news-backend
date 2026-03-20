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
            return frozen_now

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
