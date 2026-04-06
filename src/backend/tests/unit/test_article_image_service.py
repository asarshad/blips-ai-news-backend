from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.article_hydration import ArticleImageLLMExtractionResult
from app.extraction.metadata import PageMetadata
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services import article_image_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _recent_dt(*, hours_ago: int = 0, minutes_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(hours=hours_ago, minutes=minutes_ago)


def test_repair_article_image_metadata_backfills_recent_article_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Ars Technica",
        source_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Support wait times backfire",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url="https://example.com/canonical-story",
            image_url="https://cdn.example.com/hero.jpg",
            image_source="og",
        ),
    )

    result = article_image_service.repair_article_image_metadata(db, lookback_days=14, limit=50)
    repaired = db.get(ContentItem, item.id)

    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert repaired.image_url == "https://cdn.example.com/hero.jpg"
    assert repaired.canonical_url == "https://example.com/canonical-story"
    assert repaired.article_image_status == "VERIFIED"


def test_repair_article_image_metadata_skips_when_no_metadata_found(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="No metadata available",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: None,
    )

    result = article_image_service.repair_article_image_metadata(db, lookback_days=14, limit=50)
    untouched = db.get(ContentItem, item.id)

    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert result["verified_missing"] == 1
    assert untouched.image_url is None
    assert untouched.canonical_url is None
    assert untouched.article_image_status == "MISSING"


def test_repair_article_image_metadata_uses_llm_fallback_when_metadata_has_no_image(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Fallback image available via LLM",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url=article_url,
            image_url=None,
            image_source="none",
        ),
    )
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.extract_article_image_with_llm",
        lambda self, **_kwargs: "https://cdn.example.com/hero.jpg",
    )

    result = article_image_service.repair_article_image_metadata(db, lookback_days=14, limit=50)
    repaired = db.get(ContentItem, item.id)

    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert result["filled_missing"] == 1
    assert repaired.image_url == "https://cdn.example.com/hero.jpg"
    assert repaired.article_image_status == "VERIFIED"


def test_evaluate_llm_article_image_recovery_reports_success_rate(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    first = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/one",
        canonical_url="https://example.com/one",
        published_at=_recent_dt(hours_ago=2),
        title="One",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    second = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/two",
        canonical_url="https://example.com/two",
        published_at=_recent_dt(hours_ago=3),
        title="Two",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=2, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=2, minutes_ago=55),
    )
    db.add_all([first, second])
    db.commit()

    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.extract_article_image_with_llm_diagnostics",
        lambda self, article_url, title: ArticleImageLLMExtractionResult(
            image_url="https://cdn.example.com/recovered.jpg"
            if article_url.endswith("/one")
            else None,
            reason="fresh_page_metadata" if article_url.endswith("/one") else "llm_returned_none",
        ),
    )

    result = article_image_service.evaluate_llm_article_image_recovery(
        db,
        lookback_days=14,
        limit=50,
        sample_size=5,
        apply=False,
    )

    assert result["scanned"] == 2
    assert result["recovered"] == 1
    assert result["not_recovered"] == 1
    assert result["applied"] == 0
    assert result["success_rate_percent"] == 50.0
    assert result["reason_counts"] == {"fresh_page_metadata": 1, "llm_returned_none": 1}
    assert result["domain_breakdown"][0]["host"] == "example.com"


def test_evaluate_llm_article_image_recovery_reports_failure_reasons(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/one",
        canonical_url="https://example.com/one",
        published_at=_recent_dt(hours_ago=2),
        title="One",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.extract_article_image_with_llm_diagnostics",
        lambda self, article_url, title: ArticleImageLLMExtractionResult(
            image_url=None,
            reason="llm_not_configured",
            error="missing OPENAI_API_KEY",
        ),
    )

    result = article_image_service.evaluate_llm_article_image_recovery(
        db,
        lookback_days=14,
        limit=50,
        sample_size=5,
        apply=False,
    )

    assert result["scanned"] == 1
    assert result["recovered"] == 0
    assert result["failures"] == 0
    assert result["reason_counts"] == {"llm_not_configured": 1}
    assert result["failure_examples"][0]["reason"] == "llm_not_configured"
    assert result["failure_examples"][0]["error"] == "missing OPENAI_API_KEY"


def test_repair_article_image_metadata_replaces_generic_images(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="OpenUI",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        image_url="https://cdn.example.com/social-share.png",
        published_at=_recent_dt(hours_ago=2),
        title="Replaced generic image",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url=article_url,
            image_url="https://cdn.example.com/article-hero.jpg",
            image_source="body",
        ),
    )

    result = article_image_service.repair_article_image_metadata(
        db,
        lookback_days=14,
        limit=50,
        include_generic=True,
    )
    repaired = db.get(ContentItem, item.id)

    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert result["replaced_generic"] == 1
    assert repaired.image_url == "https://cdn.example.com/article-hero.jpg"
    assert repaired.article_image_status == "VERIFIED"


def test_repair_article_image_metadata_replaces_suspicious_images(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="The Verge",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        image_url="https://metrics.example.com/g/collect?tid=G-TEST&cid=123",
        published_at=_recent_dt(hours_ago=2),
        title="Replaced suspicious image",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url=article_url,
            image_url="https://cdn.example.com/article-hero.jpg",
            image_source="og",
            image_confidence="high",
        ),
    )

    result = article_image_service.repair_article_image_metadata(db, lookback_days=14, limit=50)
    repaired = db.get(ContentItem, item.id)

    assert result["scanned"] == 1
    assert result["updated"] == 1
    assert result["replaced_suspicious"] == 1
    assert repaired.image_url == "https://cdn.example.com/article-hero.jpg"


def test_repair_single_article_image_forces_reconcile(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        id=294886,
        type=ContentType.ARTICLE,
        source="Engadget",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        image_url="https://cdn.example.com/wrong-small.jpg",
        published_at=_recent_dt(hours_ago=5),
        title="Backrooms",
        curation_status=ContentStatus.PROMOTED,
        created_at=_recent_dt(hours_ago=4, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=4, minutes_ago=55),
        article_image_status="VERIFIED",
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.refresh_existing_article_metadata",
        lambda self, item, source_url, force_reconcile_image: (
            setattr(item, "image_url", "https://cdn.example.com/correct-hero.jpg") or True
        ),
    )

    result = article_image_service.repair_single_article_image(db, content_id=294886)
    repaired = db.get(ContentItem, 294886)

    assert result["content_id"] == 294886
    assert result["changed"] is True
    assert result["previous_image_url"] == "https://cdn.example.com/wrong-small.jpg"
    assert result["image_url"] == "https://cdn.example.com/correct-hero.jpg"
    assert repaired.image_url == "https://cdn.example.com/correct-hero.jpg"
    assert repaired.article_image_status == "VERIFIED"
    assert result["cache_refresh"]["content_ids"] == 1


def test_fetch_article_page_metadata_uses_final_fetched_url_for_relative_assets(monkeypatch):
    from app.extraction.fetcher import FetchResult

    html = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="/story/final" />
  <meta property="og:image" content="/images/hero.jpg" />
</head>
<body><article><p>Story</p></article></body>
</html>"""

    monkeypatch.setattr(
        "app.extraction.fetcher.fetch_url",
        lambda article_url: FetchResult(
            url="https://www.example.com/story/final",
            status_code=200,
            html=html,
            content_type="text/html",
        ),
    )

    metadata = article_image_service.fetch_article_page_metadata("https://example.com/story")

    assert metadata is not None
    assert metadata.canonical_url == "https://www.example.com/story/final"
    assert metadata.image_url == "https://www.example.com/images/hero.jpg"
