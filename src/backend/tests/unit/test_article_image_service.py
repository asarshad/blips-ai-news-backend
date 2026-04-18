from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.article_hydration import ArticleImageLLMExtractionResult
from app.extraction.metadata import PageMetadata
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services import article_image_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _recent_dt(*, hours_ago: int = 0, minutes_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(hours=hours_ago, minutes=minutes_ago)


def _create_test_tables(engine) -> None:
    ContentItem.__table__.create(bind=engine)
    ContentEventOutbox.__table__.create(bind=engine)


def test_repair_article_image_metadata_backfills_recent_article_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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


def test_repair_article_image_metadata_can_focus_on_promoted_missing_image_backlog(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    promoted_pending = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/promoted",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted pending image",
        curation_status=ContentStatus.PROMOTED,
        readiness_reason="missing_article_image",
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    candidate_item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/candidate",
        published_at=_recent_dt(hours_ago=2),
        title="Candidate pending image",
        curation_status=ContentStatus.CANDIDATE,
        readiness_reason="missing_article_image",
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    wrong_reason = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/summary",
        published_at=_recent_dt(hours_ago=2),
        title="Promoted wrong backlog",
        curation_status=ContentStatus.PROMOTED,
        readiness_reason="missing_article_summary",
        created_at=_recent_dt(hours_ago=1, minutes_ago=55),
        updated_at=_recent_dt(hours_ago=1, minutes_ago=55),
    )
    db.add_all([promoted_pending, candidate_item, wrong_reason])
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url=article_url,
            image_url="https://cdn.example.com/hero.jpg",
            image_source="og",
        ),
    )

    result = article_image_service.repair_article_image_metadata(
        db,
        lookback_days=7,
        limit=50,
        promoted_only=True,
        readiness_reasons=("missing_article_image", "awaiting_article_image_verification"),
    )

    assert result["scanned"] == 1
    assert db.get(ContentItem, promoted_pending.id).image_url == "https://cdn.example.com/hero.jpg"
    assert db.get(ContentItem, candidate_item.id).image_url is None
    assert db.get(ContentItem, wrong_reason.id).image_url is None


def test_evaluate_llm_article_image_recovery_reports_success_rate(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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
    _create_test_tables(engine)
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


def test_queue_article_image_verification_request_enqueues_for_promoted_pending_article():
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/pending",
        canonical_url="https://example.com/pending",
        published_at=_recent_dt(hours_ago=1),
        title="Pending image article",
        curation_status=ContentStatus.PROMOTED,
        readiness_status="PENDING",
        readiness_reason="awaiting_article_image_verification",
        created_at=_recent_dt(hours_ago=1),
        updated_at=_recent_dt(hours_ago=1),
    )
    db.add(item)
    db.commit()

    queued = article_image_service.queue_article_image_verification_request(db, item)
    db.commit()

    assert queued is not None
    rows = db.query(ContentEventOutbox).all()
    assert len(rows) == 1
    assert rows[0].event_type == article_image_service.ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
    assert rows[0].content_item_id == item.id


def test_process_article_image_verification_request_updates_article_and_readiness(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="Example",
        source_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=_recent_dt(hours_ago=2),
        title="Verify me",
        curation_status=ContentStatus.PROMOTED,
        article_image_status="PENDING",
        ai_processed=True,
        summary="A real summary is already present.",
        readiness_status="PENDING",
        readiness_reason="awaiting_article_image_verification",
        created_at=_recent_dt(hours_ago=2),
        updated_at=_recent_dt(hours_ago=2),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: PageMetadata(
            canonical_url=article_url,
            image_url="https://cdn.example.com/verified.jpg",
            image_source="og",
        ),
    )

    result = article_image_service.process_article_image_verification_request(
        db,
        content_id=item.id,
    )
    db.commit()
    repaired = db.get(ContentItem, item.id)
    outbox_rows = db.query(ContentEventOutbox).all()

    assert result["changed"] is True
    assert repaired.image_url == "https://cdn.example.com/verified.jpg"
    assert repaired.article_image_status == "VERIFIED"
    assert repaired.readiness_status == "READY"
    assert any(row.event_type == "content.ready" for row in outbox_rows)


def test_repair_article_image_metadata_falls_back_to_source_placeholder_after_prior_attempt(
    monkeypatch,
):
    """When real-image recovery is exhausted and a prior verification attempt
    is old enough, a source-branded placeholder is written so the article can
    leave ``missing_article_image`` and reach READY."""
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    prior_attempt_at = datetime.utcnow() - timedelta(minutes=45)
    item = ContentItem(
        type=ContentType.ARTICLE,
        source="InfoQ",
        source_url="https://example.com/infoq-story",
        canonical_url="https://example.com/infoq-story",
        published_at=_recent_dt(hours_ago=2),
        title="InfoQ article waiting on placeholder",
        curation_status=ContentStatus.PROMOTED,
        readiness_reason="missing_article_image",
        article_image_status="MISSING",
        article_image_checked_at=prior_attempt_at,
        topics=["technology"],
        created_at=_recent_dt(hours_ago=2),
        updated_at=_recent_dt(hours_ago=2),
    )
    db.add(item)
    db.commit()

    # Real-image recovery all returns nothing.
    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: None,
    )
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.extract_article_image_with_llm",
        lambda self, **_kwargs: None,
    )

    result = article_image_service.repair_article_image_metadata(
        db,
        lookback_days=7,
        limit=50,
        promoted_only=True,
        readiness_reasons=("missing_article_image", "awaiting_article_image_verification"),
    )
    repaired = db.get(ContentItem, item.id)

    assert result["placeholder_applied"] == 1
    assert repaired.image_url is not None
    assert "/placeholder/source" in repaired.image_url
    assert "source=InfoQ" in repaired.image_url
    assert "category=technology" in repaired.image_url
    # With a placeholder URL in place, the verification outcome is VERIFIED
    # (placeholder URLs are accepted by finalize_article_image_verification).
    assert repaired.article_image_status == "VERIFIED"


def test_repair_article_image_metadata_defers_placeholder_when_no_prior_attempt(
    monkeypatch,
):
    """Fresh articles with no verification history should NOT immediately get
    a placeholder — the recovery loop needs at least one prior real attempt."""
    engine = create_engine("sqlite:///:memory:")
    _create_test_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.ARTICLE,
        source="InfoQ",
        source_url="https://example.com/new-story",
        canonical_url="https://example.com/new-story",
        published_at=_recent_dt(hours_ago=1),
        title="Brand new InfoQ article",
        curation_status=ContentStatus.PROMOTED,
        readiness_reason="missing_article_image",
        article_image_status="PENDING",
        article_image_checked_at=None,
        topics=["technology"],
        created_at=_recent_dt(minutes_ago=30),
        updated_at=_recent_dt(minutes_ago=30),
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        article_image_service,
        "fetch_article_page_metadata",
        lambda article_url: None,
    )
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService.extract_article_image_with_llm",
        lambda self, **_kwargs: None,
    )

    result = article_image_service.repair_article_image_metadata(
        db,
        lookback_days=7,
        limit=50,
        promoted_only=True,
        readiness_reasons=("missing_article_image", "awaiting_article_image_verification"),
    )
    repaired = db.get(ContentItem, item.id)

    # First pass records the attempt but does not commit a placeholder.
    assert result["placeholder_applied"] == 0
    assert repaired.image_url is None
    # After the first attempt, checked_at is populated so the next run
    # (once enough time has passed) can apply the placeholder.
    assert repaired.article_image_checked_at is not None
