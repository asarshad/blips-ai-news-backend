"""Tests for language filtering in the ingestion pipeline.

Verifies that:
- English content passes through and gets language='en'
- Non-English content is rejected early (before LLM/DB)
- Short/ambiguous text passes with safe-default language='en'
- content_filtered_language_total counter increments on rejections
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from unittest.mock import MagicMock, patch

from app.extraction.metrics import ExtractionMetrics
from app.models.content import ContentType

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_feed_entry(
    title: str,
    content: Optional[str] = None,
    url: str = "https://example.com/article",
    image_url: Optional[str] = None,
    published_date: Optional[datetime] = None,
) -> MagicMock:
    """Build a minimal FeedEntry mock."""
    entry = MagicMock()
    entry.title = title
    entry.content = content or ""
    entry.url = url
    entry.image_url = image_url
    entry.published_date = published_date or datetime(2026, 3, 1, 12, 0, 0)
    entry.feed_name = "test-feed"
    entry.feed_role = None
    entry.base_quality_weight = None
    entry.quality_tier = None
    return entry


def _make_video_entry(
    title: str,
    summary: Optional[str] = None,
    video_id: str = "abc123",
    video_url: str = "https://youtube.com/watch?v=abc123",
    thumbnail_url: Optional[str] = None,
    source: str = "YouTube",
) -> MagicMock:
    """Build a minimal VideoEntry mock."""
    entry = MagicMock()
    entry.title = title
    entry.summary = summary or ""
    entry.video_id = video_id
    entry.video_url = video_url
    entry.thumbnail_url = thumbnail_url
    entry.source = source
    entry.is_short = False
    entry.quality_tier = None
    return entry


def _make_pipeline(metrics: Optional[ExtractionMetrics] = None):
    """Build a minimal IngestionPipeline with all dependencies mocked."""
    from app.ingestion.service import IngestionPipeline

    db = MagicMock()
    content_repo = MagicMock()
    # Simulate no existing item for dedup checks
    content_repo.get_by_source_url.return_value = None
    content_repo.get_by_dedupe_key.return_value = None
    content_repo.get_by_canonical_url.return_value = None

    clustering = MagicMock()
    scoring = MagicMock()

    rss_client = MagicMock()
    youtube_client = MagicMock()
    youtube_client.get_video_duration.return_value = None
    youtube_client.get_transcript.return_value = None

    llm_client = MagicMock()
    llm_client.is_configured.return_value = False  # Skip LLM to keep tests fast

    pipeline = IngestionPipeline(
        db=db,
        content_repo=content_repo,
        clustering_service=clustering,
        scoring_service=scoring,
        rss_client=rss_client,
        youtube_client=youtube_client,
        llm_client=llm_client,
    )

    return pipeline


# ── RSS ingestion language tests ─────────────────────────────────────────────


class TestRssIngestionLanguageFilter:
    """Language filter is applied early in ingest_rss_entry."""

    def test_english_article_passes(self):
        """English article is ingested and language='en' is stored."""
        pipeline = _make_pipeline()
        entry = _make_feed_entry(
            title="OpenAI releases GPT-5 with unprecedented reasoning ability",
            content="The model demonstrates significant advances in logic and coding tasks.",
        )

        # Patch extraction pipeline to return quickly
        with patch("app.ingestion.service.run_extraction") as mock_extract:
            mock_result = MagicMock()
            mock_result.main_text = "Full article text here."
            mock_result.title = entry.title
            mock_result.image_url = None
            mock_result.canonical_url = None
            mock_result.published_at = None
            mock_result.excerpt_fallback = None
            mock_extract.return_value = mock_result

            with patch("app.ingestion.service.extraction_metrics"):
                pipeline.ingest_rss_entry(entry)

        # The ContentItem was added to the DB session
        assert pipeline.db.add.called
        added = pipeline.db.add.call_args[0][0]
        assert added.language == "en"

    def test_spanish_article_is_rejected(self, caplog):
        """Non-English article is rejected before extraction/LLM."""
        import logging

        pipeline = _make_pipeline()
        entry = _make_feed_entry(
            title="Las mejores aplicaciones para tu teléfono Android en 2026",
            content=(
                "Descubre las mejores apps para Android este año. "
                "Te ofrecemos una selección completa de las aplicaciones más útiles."
            ),
        )

        with patch("app.ingestion.service.run_extraction") as mock_extract:
            with patch("app.ingestion.service.extraction_metrics") as mock_metrics:
                with caplog.at_level(logging.INFO, logger="app.ingestion.service"):
                    result = pipeline.ingest_rss_entry(entry)

        assert result is None
        mock_extract.assert_not_called()  # Rejected before extraction
        mock_metrics.record_language_filtered.assert_called_once()

    def test_short_title_passes_as_safe_default(self):
        """Titles too short for reliable detection pass through safely."""
        pipeline = _make_pipeline()
        entry = _make_feed_entry(title="AI")  # Well under _MIN_DETECT_LENGTH

        with patch("app.ingestion.service.run_extraction") as mock_extract:
            mock_result = MagicMock()
            mock_result.main_text = None
            mock_result.title = entry.title
            mock_result.image_url = None
            mock_result.canonical_url = None
            mock_result.published_at = None
            mock_result.excerpt_fallback = None
            mock_extract.return_value = mock_result

            with patch("app.ingestion.service.extraction_metrics"):
                pipeline.ingest_rss_entry(entry)

        # Short text → safe default → should reach DB add
        assert pipeline.db.add.called
        added = pipeline.db.add.call_args[0][0]
        # language defaults to "en" when detection is inconclusive
        assert added.language == "en"

    def test_language_filter_before_extraction(self):
        """Extraction pipeline must NOT be called for non-English content."""
        pipeline = _make_pipeline()
        entry = _make_feed_entry(
            title="Revisión completa del nuevo procesador Intel en 2026",
            content="Un análisis detallado del procesador Intel con benchmarks extensivos.",
        )

        with patch("app.ingestion.service.run_extraction") as mock_extract:
            with patch("app.ingestion.service.extraction_metrics"):
                pipeline.ingest_rss_entry(entry)

        mock_extract.assert_not_called()

    def test_language_filter_before_llm(self):
        """LLM summarization must NOT be called for non-English content."""
        pipeline = _make_pipeline()
        pipeline.llm_client.is_configured.return_value = True
        entry = _make_feed_entry(
            title="El nuevo modelo de IA supera todas las expectativas este año",
            content="Los investigadores presentaron resultados sorprendentes del modelo.",
        )

        with patch("app.ingestion.service.run_extraction"):
            with patch("app.ingestion.service.extraction_metrics"):
                pipeline.ingest_rss_entry(entry)

        pipeline.llm_client.summarize_article.assert_not_called()


# ── YouTube ingestion language tests ─────────────────────────────────────────


class TestYouTubeIngestionLanguageFilter:
    """Language filter is applied early in ingest_youtube_entry."""

    def test_english_video_passes(self):
        """English video is ingested and language='en' is stored."""
        pipeline = _make_pipeline()
        entry = _make_video_entry(
            title="We tested the new M5 MacBook Pro — here's what changed",
            summary="In this review we benchmark the new Apple silicon chip.",
        )

        with patch("app.ingestion.service.extraction_metrics"):
            pipeline.ingest_youtube_entry(entry)

        assert pipeline.db.add.called
        added = pipeline.db.add.call_args[0][0]
        assert added.language == "en"

    def test_spanish_video_is_rejected(self):
        """Non-English video is rejected before LLM and DB insert."""
        pipeline = _make_pipeline()
        entry = _make_video_entry(
            title="Revisión completa del procesador Intel Core Ultra 300",
            summary="En este análisis revisamos el nuevo chip de Intel con benchmarks.",
        )

        with patch("app.ingestion.service.extraction_metrics") as mock_metrics:
            result = pipeline.ingest_youtube_entry(entry)

        assert result is None
        assert not pipeline.db.add.called
        mock_metrics.record_language_filtered.assert_called_once()

    def test_short_video_title_passes_as_safe_default(self):
        """Videos with titles too short for detection pass safely."""
        pipeline = _make_pipeline()
        entry = _make_video_entry(title="AI")

        with patch("app.ingestion.service.extraction_metrics"):
            pipeline.ingest_youtube_entry(entry)

        assert pipeline.db.add.called
        added = pipeline.db.add.call_args[0][0]
        assert added.language == "en"

    def test_language_filter_before_llm_for_video(self):
        """LLM must NOT be called for non-English videos."""
        pipeline = _make_pipeline()
        pipeline.llm_client.is_configured.return_value = True
        entry = _make_video_entry(
            title="¡Las mejores GPUs de 2026 para gaming y edición de video!",
            summary="Analizamos las mejores tarjetas gráficas disponibles para gamers.",
        )

        with patch("app.ingestion.service.extraction_metrics"):
            pipeline.ingest_youtube_entry(entry)

        pipeline.llm_client.summarize_video.assert_not_called()

    def test_explicit_shorts_url_with_134_seconds_is_stored_as_reel(self):
        """3-minute-era Shorts URLs should still land on the reels surface."""
        pipeline = _make_pipeline()
        pipeline.youtube_client.get_video_duration.return_value = 134
        entry = _make_video_entry(
            title="Why I intentionally misuse my fitness tracker",
            video_id="VvGaDPViMKY",
            video_url="https://www.youtube.com/shorts/VvGaDPViMKY",
            source="The Verge",
        )

        with patch("app.ingestion.service.extraction_metrics"):
            pipeline.ingest_youtube_entry(entry)

        added = pipeline.db.add.call_args[0][0]
        assert added.type == ContentType.REEL


# ── Metrics counter tests ─────────────────────────────────────────────────────


class TestLanguageFilterMetricsCounter:
    """content_filtered_language_total counter increments correctly."""

    def test_counter_starts_at_zero(self):
        m = ExtractionMetrics()
        counters = m.get_counters()
        assert counters["content_filtered_language_total"] == 0

    def test_counter_increments_on_rejection(self):
        m = ExtractionMetrics()
        m.record_language_filtered("es")
        counters = m.get_counters()
        assert counters["content_filtered_language_total"] == 1

    def test_counter_accumulates_multiple_rejections(self):
        m = ExtractionMetrics()
        for lang in ["es", "fr", "de", "pt", "ja"]:
            m.record_language_filtered(lang)
        counters = m.get_counters()
        assert counters["content_filtered_language_total"] == 5

    def test_counter_is_cumulative_not_daily(self):
        """Language filter counter must NOT reset on day rollover."""
        from datetime import timedelta

        m = ExtractionMetrics()
        m.record_language_filtered("es")

        # Simulate day rollover
        m._day = m._day - timedelta(days=1)
        m.record_language_filtered("fr")  # triggers _maybe_reset_day internally

        # Counter should be 2, not 1 (not reset by day rollover)
        counters = m.get_counters()
        assert counters["content_filtered_language_total"] == 2
