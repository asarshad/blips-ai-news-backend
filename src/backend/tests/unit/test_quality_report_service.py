from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.ingestion_budget import IngestionBudget
from app.quality.report_service import build_quality_report


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


@compiles(PgEnum, "sqlite")
def _compile_enum_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_build_quality_report_counts_suppressed_rows():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    IngestionBudget.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    day = date(2026, 3, 20)
    now = datetime(2026, 3, 20, 12, 0, 0)

    db.add(
        IngestionBudget(
            day=day,
            content_type=ContentType.ARTICLE,
            target=5,
            reserved=1,
            inserted=1,
            seen=2,
            suppressed=1,
            attempts=2,
            updated_at=now,
        )
    )
    db.add_all(
        [
            ContentItem(
                type=ContentType.ARTICLE,
                source="Primary Source",
                source_url="https://example.com/article-1",
                canonical_url="https://example.com/article-1",
                published_at=now,
                title="Visible article",
                curation_status=ContentStatus.PROMOTED,
                is_suppressed=False,
                content_text="body",
                created_at=now,
                updated_at=now,
                ingestion_day=day,
            ),
            ContentItem(
                type=ContentType.ARTICLE,
                source="Secondary Source",
                source_url="https://example.com/article-2",
                canonical_url="https://example.com/article-2",
                published_at=now,
                title="Suppressed article",
                curation_status=ContentStatus.PROMOTED,
                is_suppressed=True,
                created_at=now,
                updated_at=now,
                ingestion_day=day,
            ),
        ]
    )
    db.commit()

    report = build_quality_report(db, day=day)
    article_stats = next(row for row in report["types"] if row["content_type"] == "ARTICLE")

    assert article_stats["total"] == 2
    assert article_stats["suppressed"] == 1
    assert article_stats["inserted"] == 1
    assert article_stats["top_source"] == "Primary Source"
    assert report["extraction"]["article_total"] == 1
    assert report["extraction"]["article_with_text"] == 1
