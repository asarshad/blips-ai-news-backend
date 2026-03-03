"""Tests for app.extraction.metadata — HTML head metadata extraction."""


from app.extraction.metadata import PageMetadata, extract_metadata


def _html_with_head(head_content: str) -> str:
    """Build a minimal HTML doc with the given <head> content."""
    return f"""<!DOCTYPE html>
<html><head>{head_content}</head><body><p>Body</p></body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical URL
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalUrl:

    def test_absolute_canonical(self):
        html = _html_with_head('<link rel="canonical" href="https://example.com/article/1" />')
        meta = extract_metadata(html, "https://example.com/article/1?utm=xyz")
        assert meta.canonical_url == "https://example.com/article/1"

    def test_relative_canonical_resolved(self):
        html = _html_with_head('<link rel="canonical" href="/article/1" />')
        meta = extract_metadata(html, "https://example.com/section/old")
        assert meta.canonical_url == "https://example.com/article/1"

    def test_no_canonical_returns_none(self):
        html = _html_with_head("<title>No Canonical</title>")
        meta = extract_metadata(html, "https://example.com/page")
        assert meta.canonical_url is None

    def test_empty_canonical_href(self):
        html = _html_with_head('<link rel="canonical" href="" />')
        meta = extract_metadata(html, "https://example.com/page")
        # Empty href should not produce canonical
        assert meta.canonical_url is None


# ═══════════════════════════════════════════════════════════════════════════════
# Title
# ═══════════════════════════════════════════════════════════════════════════════


class TestTitleExtraction:

    def test_og_title_preferred(self):
        html = _html_with_head(
            '<meta property="og:title" content="OG Title" />'
            '<meta name="twitter:title" content="Twitter Title" />'
            "<title>HTML Title</title>"
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "OG Title"

    def test_twitter_title_fallback(self):
        html = _html_with_head(
            '<meta name="twitter:title" content="Twitter Title" />'
            "<title>HTML Title</title>"
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "Twitter Title"

    def test_html_title_fallback(self):
        html = _html_with_head("<title>HTML Title</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "HTML Title"

    def test_no_title(self):
        html = _html_with_head("")
        meta = extract_metadata(html, "https://example.com")
        assert meta.title is None


# ═══════════════════════════════════════════════════════════════════════════════
# Image URL
# ═══════════════════════════════════════════════════════════════════════════════


class TestImageExtraction:

    def test_og_image_preferred(self):
        html = _html_with_head(
            '<meta property="og:image" content="https://cdn.example.com/og.jpg" />'
            '<meta name="twitter:image" content="https://cdn.example.com/tw.jpg" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url == "https://cdn.example.com/og.jpg"
        assert meta.image_source == "og"

    def test_twitter_image_fallback(self):
        html = _html_with_head(
            '<meta name="twitter:image" content="https://cdn.example.com/tw.jpg" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url == "https://cdn.example.com/tw.jpg"
        assert meta.image_source == "twitter"

    def test_relative_image_made_absolute(self):
        html = _html_with_head(
            '<meta property="og:image" content="/images/hero.jpg" />'
        )
        meta = extract_metadata(html, "https://example.com/article")
        assert meta.image_url == "https://example.com/images/hero.jpg"
        assert meta.image_source == "og"

    def test_data_uri_image_rejected(self):
        html = _html_with_head(
            '<meta property="og:image" content="data:image/png;base64,abc" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url is None
        assert meta.image_source == "none"

    def test_no_image(self):
        html = _html_with_head("<title>No Image</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url is None
        assert meta.image_source == "none"


# ═══════════════════════════════════════════════════════════════════════════════
# Published date
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishedDate:

    def test_article_published_time(self):
        html = _html_with_head(
            '<meta property="article:published_time" content="2024-06-15T10:30:00Z" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str == "2024-06-15T10:30:00Z"

    def test_date_published(self):
        html = _html_with_head(
            '<meta name="datePublished" content="2024-01-01" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str == "2024-01-01"

    def test_no_date(self):
        html = _html_with_head("<title>No Date</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str is None


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadataEdgeCases:

    def test_empty_html(self):
        meta = extract_metadata("", "https://example.com")
        assert meta.canonical_url is None
        assert meta.title is None
        assert meta.image_url is None

    def test_garbage_html(self):
        meta = extract_metadata("<<<not html at all>>>", "https://example.com")
        # Should not raise, returns defaults
        assert isinstance(meta, PageMetadata)

    def test_description_extraction(self):
        html = _html_with_head(
            '<meta property="og:description" content="OG description here" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.description == "OG description here"
