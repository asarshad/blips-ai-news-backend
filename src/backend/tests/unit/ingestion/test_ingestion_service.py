from unittest.mock import MagicMock

from app.ingestion import service as ingestion_service
from app.models.content import ContentType


def test_run_video_discovery_ingestion_uses_pipeline(monkeypatch):
    calls = []

    class _FakePipeline:
        def ingest_video_discovery_candidates(self):
            calls.append("called")
            return {"status": "ok", "videos_ingested": 2, "reels_ingested": 1}

    monkeypatch.setattr(ingestion_service, "create_ingestion_pipeline", lambda db: _FakePipeline())

    result = ingestion_service.run_video_discovery_ingestion(db=object())

    assert calls == ["called"]
    assert result["videos_ingested"] == 2
    assert result["reels_ingested"] == 1


def test_discovery_remaining_needed_uses_fresh_promoted_gap_for_videos():
    pipeline = ingestion_service.IngestionPipeline.__new__(ingestion_service.IngestionPipeline)
    pipeline._fresh_promoted_inventory_count = lambda content_type: 14

    remaining = pipeline._discovery_remaining_needed(
        ContentType.VIDEO,
        created_remaining=0,
    )

    assert remaining == 46


def test_discovery_remaining_needed_prefers_created_gap_when_larger():
    pipeline = ingestion_service.IngestionPipeline.__new__(ingestion_service.IngestionPipeline)
    pipeline._fresh_promoted_inventory_count = lambda content_type: 34

    remaining = pipeline._discovery_remaining_needed(
        ContentType.REEL,
        created_remaining=9,
    )

    assert remaining == 9


def _make_pipeline():
    db = MagicMock()
    content_repo = MagicMock()
    content_repo.get_by_source_url.return_value = None
    content_repo.get_by_dedupe_key.return_value = None
    content_repo.get_by_canonical_url.return_value = None

    return ingestion_service.IngestionPipeline(
        db=db,
        content_repo=content_repo,
        clustering_service=MagicMock(),
        scoring_service=MagicMock(),
        rss_client=MagicMock(),
        youtube_client=MagicMock(),
        llm_client=MagicMock(),
    )


def _make_entry(
    *,
    title: str = "Example article",
    content: str = "Article body",
    url: str = "https://example.com/story",
    image_url: str | None = None,
):
    entry = MagicMock()
    entry.title = title
    entry.content = content
    entry.url = url
    entry.image_url = image_url
    entry.published_date = None
    entry.feed_name = "test-feed"
    entry.feed_role = None
    entry.base_quality_weight = None
    entry.quality_tier = None
    return entry


def test_ingest_rss_entry_refreshes_existing_duplicate_image_from_page_metadata(monkeypatch):
    pipeline = _make_pipeline()
    existing = MagicMock()
    existing.image_url = None
    existing.canonical_url = None
    pipeline.content_repo.get_by_source_url.return_value = existing

    entry = _make_entry()

    meta = MagicMock(
        image_url="https://cdn.example.com/hero.jpg",
        canonical_url="https://example.com/canonical-story",
    )
    monkeypatch.setattr(
        pipeline.article_hydrator,
        "fetch_article_page_metadata",
        lambda source_url: meta,
    )

    result = pipeline.ingest_rss_entry(entry)

    assert result is None
    assert existing.image_url == "https://cdn.example.com/hero.jpg"
    assert existing.canonical_url == "https://example.com/canonical-story"
    pipeline.db.commit.assert_called_once()
    pipeline.db.refresh.assert_called_once_with(existing)


def test_ingest_rss_entry_refreshes_duplicate_from_rss_image_without_fetch(monkeypatch):
    pipeline = _make_pipeline()
    existing = MagicMock()
    existing.image_url = None
    existing.canonical_url = "https://example.com/story"
    pipeline.content_repo.get_by_source_url.return_value = existing

    entry = _make_entry(image_url="https://cdn.example.com/rss-hero.jpg")

    fetch_calls = []

    def _unexpected_fetch(source_url):
        fetch_calls.append(source_url)
        return None

    monkeypatch.setattr(
        pipeline.article_hydrator,
        "fetch_article_page_metadata",
        _unexpected_fetch,
    )

    result = pipeline.ingest_rss_entry(entry)

    assert result is None
    assert existing.image_url == "https://cdn.example.com/rss-hero.jpg"
    assert fetch_calls == []
    pipeline.db.commit.assert_called_once()
