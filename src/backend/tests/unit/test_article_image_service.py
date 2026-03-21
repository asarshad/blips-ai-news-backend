from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.extraction.metadata import PageMetadata
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services import article_image_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_repair_article_image_metadata_backfills_recent_article_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Ars Technica",
        source_url="https://example.com/story",
        published_at=datetime(2026, 3, 20, 10, 0, 0),
        title="Support wait times backfire",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 20, 10, 5, 0),
        updated_at=datetime(2026, 3, 20, 10, 5, 0),
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


def test_repair_article_image_metadata_skips_when_no_metadata_found(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        published_at=datetime(2026, 3, 20, 10, 0, 0),
        title="No metadata available",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 20, 10, 5, 0),
        updated_at=datetime(2026, 3, 20, 10, 5, 0),
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
    assert result["updated"] == 0
    assert untouched.image_url is None
    assert untouched.canonical_url is None


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
        published_at=datetime(2026, 3, 20, 10, 0, 0),
        title="Replaced generic image",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 20, 10, 5, 0),
        updated_at=datetime(2026, 3, 20, 10, 5, 0),
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
        article_image_service,
        "fetch_url",
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
