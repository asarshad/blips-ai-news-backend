"""Tests for app.extraction.pipeline — full extraction pipeline."""

from datetime import datetime
from unittest.mock import patch

from app.extraction.pipeline import (
    ExtractionResult,
    ExtractionStatus,
    ImageStatus,
    RSSEntryData,
    _try_parse_date,
    run_extraction,
)

# fetch_url is imported lazily inside run_extraction via:
#   from app.extraction.fetcher import fetch_url
# so to mock it we must patch at the fetcher module level.
_FETCH_URL_PATCH = "app.extraction.fetcher.fetch_url"


# ═══════════════════════════════════════════════════════════════════════════════
# _try_parse_date
# ═══════════════════════════════════════════════════════════════════════════════


class TestTryParseDate:
    def test_iso_with_timezone(self):
        dt = _try_parse_date("2024-06-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

    def test_iso_without_timezone(self):
        dt = _try_parse_date("2024-06-15T10:30:00")
        assert dt is not None

    def test_date_only(self):
        dt = _try_parse_date("2024-06-15")
        assert dt is not None
        assert dt.year == 2024

    def test_slash_format(self):
        dt = _try_parse_date("2024/06/15")
        assert dt is not None

    def test_garbage_returns_none(self):
        assert _try_parse_date("not a date") is None

    # ── Timezone-aware guarantees (new) ────────────────────────────────────

    def test_tz_aware_input_stays_tz_aware(self):
        dt = _try_parse_date("2024-06-15T10:30:00+05:30")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_tz_naive_input_gets_utc(self):
        """Tz-naive strings must be treated as UTC, not 'floating'."""
        dt = _try_parse_date("2024-06-15T10:30:00")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_date_only_gets_utc(self):
        dt = _try_parse_date("2024-06-15")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_empty_returns_none(self):
        assert _try_parse_date("") is None


# ═══════════════════════════════════════════════════════════════════════════════
# run_extraction — RSS-only (skip_fetch)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunExtractionRssOnly:
    """Test run_extraction with skip_fetch=True to test RSS fallback paths."""

    def test_rss_only_basic(self):
        rss = RSSEntryData(
            title="RSS Title",
            description="An RSS description for this test article.",
            image_url="https://example.com/rss-image.jpg",
            published_date=datetime(2024, 6, 15),
        )
        result = run_extraction(
            "https://example.com/article/1",
            rss_entry=rss,
            skip_fetch=True,
        )

        assert isinstance(result, ExtractionResult)
        assert result.source_url == "https://example.com/article/1"
        assert result.title == "RSS Title"
        assert result.published_at == datetime(2024, 6, 15)
        # RSS image should be validated
        assert result.image_url == "https://example.com/rss-image.jpg"
        assert result.image_source == "rss"
        assert result.image_status == ImageStatus.OK

    def test_rss_invalid_image_becomes_none(self):
        rss = RSSEntryData(
            title="Title",
            image_url="data:image/png;base64,abc",
        )
        result = run_extraction("https://example.com", rss_entry=rss, skip_fetch=True)
        assert result.image_url is None
        assert result.image_status == ImageStatus.INVALID

    def test_rss_no_image_missing(self):
        rss = RSSEntryData(title="Title")
        result = run_extraction("https://example.com", rss_entry=rss, skip_fetch=True)
        assert result.image_url is None
        assert result.image_status == ImageStatus.MISSING

    def test_canonical_url_fallback_to_source(self):
        result = run_extraction("https://example.com/article", skip_fetch=True)
        assert result.canonical_url == "https://example.com/article"


# ═══════════════════════════════════════════════════════════════════════════════
# run_extraction — never raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunExtractionNeverRaises:
    """run_extraction must NEVER raise — all errors captured in result."""

    @patch(_FETCH_URL_PATCH, side_effect=Exception("boom"))
    def test_fetch_exception_captured(self, mock_fetch):
        # The pipeline imports fetch_url lazily; we need to patch the right place
        result = run_extraction("https://example.com/article")
        assert isinstance(result, ExtractionResult)
        # Should not raise, error captured
        assert result.fetch_error is not None or result.extraction_status == ExtractionStatus.FAILED

    def test_empty_source_url(self):
        result = run_extraction("", skip_fetch=True)
        assert isinstance(result, ExtractionResult)


# ═══════════════════════════════════════════════════════════════════════════════
# run_extraction — with HTML (mocked fetch)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunExtractionWithFetch:
    """Test pipeline with mocked fetch returning real HTML."""

    def _make_fetch_result(self, html: str, url: str = "https://example.com"):
        from app.extraction.fetcher import FetchResult

        return FetchResult(
            url=url,
            status_code=200,
            html=html,
            content_type="text/html",
            elapsed_ms=42.0,
        )

    def _article_html(self, *, title="Test Article", image="https://cdn.example.com/hero.jpg"):
        body_text = " ".join(["Technology is transforming the world of software engineering."] * 40)
        return f"""<!DOCTYPE html>
<html>
<head>
<title>{title}</title>
<meta property="og:title" content="{title}" />
<meta property="og:image" content="{image}" />
<meta property="article:published_time" content="2024-06-15T10:00:00Z" />
<link rel="canonical" href="https://example.com/canonical-article" />
</head>
<body>
<article>
<h1>{title}</h1>
<p>{body_text}</p>
</article>
</body>
</html>"""

    @patch(_FETCH_URL_PATCH)
    def test_full_pipeline_ok(self, mock_fetch):
        html = self._article_html()
        mock_fetch.return_value = self._make_fetch_result(html)

        result = run_extraction("https://example.com/article")

        assert result.canonical_url == "https://example.com/canonical-article"
        assert result.title == "Test Article"
        assert result.image_url == "https://cdn.example.com/hero.jpg"
        assert result.image_status == ImageStatus.OK
        assert result.image_source == "og"
        assert result.image_confidence in {"medium", "high"}
        assert result.image_suspicious is False
        assert result.published_at is not None
        assert result.published_at.year == 2024

    @patch(_FETCH_URL_PATCH)
    def test_pipeline_uses_final_fetched_url_for_relative_metadata(self, mock_fetch):
        html = """<!DOCTYPE html>
<html>
<head>
<title>Redirected Article</title>
<meta property="og:title" content="Redirected Article" />
<meta property="og:image" content="/images/hero.jpg" />
<link rel="canonical" href="/story/final" />
</head>
<body>
<article><p>"""
        html += " ".join(["content"] * 200)
        html += """</p></article>
</body>
</html>"""
        mock_fetch.return_value = self._make_fetch_result(
            html,
            url="https://www.example.com/story/final",
        )

        result = run_extraction("https://example.com/story")

        assert result.canonical_url == "https://www.example.com/story/final"
        assert result.image_url == "https://www.example.com/images/hero.jpg"
        assert result.image_source == "og"

    @patch(_FETCH_URL_PATCH)
    def test_rss_fallback_when_page_has_no_image(self, mock_fetch):
        html = (
            """<!DOCTYPE html>
<html><head><title>No Image</title></head>
<body><article><p>"""
            + " ".join(["content"] * 200)
            + """</p></article></body></html>"""
        )
        mock_fetch.return_value = self._make_fetch_result(html)

        rss = RSSEntryData(image_url="https://example.com/rss-fallback.jpg")
        result = run_extraction("https://example.com/article", rss_entry=rss)

        # Page has no og:image, should fall back to RSS image
        assert result.image_url == "https://example.com/rss-fallback.jpg"
        assert result.image_source == "rss"

    @patch(_FETCH_URL_PATCH)
    def test_pipeline_prefers_rss_image_over_weak_page_og_asset(self, mock_fetch):
        html = """<!DOCTYPE html>
<html>
<head>
<title>Blocked Variant</title>
<meta property="og:image" content="https://s.yimg.com/kw/assets/engadget-amp-proposed.png" />
</head>
<body><article><p>"""
        html += " ".join(["content"] * 200)
        html += """</p></article></body></html>"""
        mock_fetch.return_value = self._make_fetch_result(html)

        rss = RSSEntryData(image_url="https://cdn.example.com/rss-hero.jpg")
        result = run_extraction("https://example.com/article", rss_entry=rss)

        assert result.image_url == "https://cdn.example.com/rss-hero.jpg"
        assert result.image_source == "rss"

    @patch(_FETCH_URL_PATCH)
    def test_fetch_error_falls_back_to_rss(self, mock_fetch):
        from app.extraction.fetcher import FetchResult

        mock_fetch.return_value = FetchResult(
            url="https://example.com",
            error="Connection timed out",
            elapsed_ms=20000,
        )

        rss = RSSEntryData(
            title="RSS Title",
            description="RSS description content.",
            image_url="https://cdn.example.com/rss.jpg",
        )
        result = run_extraction("https://example.com/article", rss_entry=rss)

        assert result.fetch_error is not None
        assert result.title == "RSS Title"
        assert result.image_url == "https://cdn.example.com/rss.jpg"


# ═══════════════════════════════════════════════════════════════════════════════
# ExtractionResult + RSSEntryData dataclasses
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataclasses:
    def test_extraction_result_defaults(self):
        r = ExtractionResult(source_url="https://example.com")
        assert r.extraction_status == ExtractionStatus.FAILED
        assert r.image_status == ImageStatus.MISSING
        assert r.main_text is None
        assert r.word_count == 0

    def test_rss_entry_data_defaults(self):
        r = RSSEntryData()
        assert r.title is None
        assert r.description is None
        assert r.image_url is None
        assert r.published_date is None
