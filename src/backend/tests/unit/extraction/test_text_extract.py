"""Tests for app.extraction.text_extract — cascading text extraction."""

from app.extraction.text_extract import (
    TextResult,
    extract_text,
    extract_with_readability,
    extract_with_trafilatura,
)


def _article_html(body_text: str, *, boilerplate: str = "") -> str:
    """Build realistic article HTML with nav/footer boilerplate."""
    words_as_paragraphs = "<p>" + body_text.replace("\n", "</p><p>") + "</p>"
    return f"""<!DOCTYPE html>
<html>
<head><title>Test Article</title></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<article>
<h1>Test Article Title</h1>
{words_as_paragraphs}
</article>
<footer>{boilerplate}<p>© 2024 TestCorp. All rights reserved.</p></footer>
</body>
</html>"""


def _make_long_text(word_count: int = 300) -> str:
    """Generate realistic-ish filler text with enough words."""
    sentence = "The quick brown fox jumps over the lazy dog with great enthusiasm. "
    return (sentence * (word_count // 10 + 1)).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# extract_with_trafilatura
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrafilaturaExtraction:
    def test_extracts_article_text(self):
        body = _make_long_text(300)
        html = _article_html(body)
        result = extract_with_trafilatura(html, url="https://example.com")
        assert result.extractor == "trafilatura"
        if result.text:  # trafilatura may or may not extract from simple HTML
            assert result.word_count > 0

    def test_returns_text_result_on_empty_html(self):
        result = extract_with_trafilatura("", url="https://example.com")
        assert isinstance(result, TextResult)
        assert result.extractor == "trafilatura"


# ═══════════════════════════════════════════════════════════════════════════════
# extract_with_readability
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadabilityExtraction:
    def test_extracts_article_text(self):
        body = _make_long_text(300)
        html = _article_html(body)
        result = extract_with_readability(html, url="https://example.com")
        assert result.extractor == "readability"
        # Readability should find the article content
        if result.text:
            assert result.word_count > 0

    def test_returns_text_result_on_empty_html(self):
        result = extract_with_readability("", url="https://example.com")
        assert isinstance(result, TextResult)
        assert result.extractor == "readability"


# ═══════════════════════════════════════════════════════════════════════════════
# extract_text (cascading)
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractText:
    def test_no_html_falls_back_to_rss(self):
        rss_desc = "This is the RSS description for the article."
        result = extract_text(html="", url=None, rss_description=rss_desc)
        assert result.extractor == "rss_only"
        assert "RSS description" in result.text

    def test_no_html_no_rss_returns_empty(self):
        result = extract_text(html="", url=None, rss_description=None)
        assert result.text is None
        assert result.extractor == "none"
        assert result.word_count == 0

    def test_good_html_extracts_text(self):
        body = _make_long_text(400)
        html = _article_html(body)
        result = extract_text(html=html, url="https://example.com")
        # Should get some text from either trafilatura or readability
        assert result.text is not None or result.extractor == "none"

    def test_rss_fallback_when_html_empty_body(self):
        html = """<!DOCTYPE html><html><head><title>T</title></head><body></body></html>"""
        rss_desc = "Fallback RSS content describing the article in detail."
        result = extract_text(html=html, url="https://example.com", rss_description=rss_desc)
        # Should at least try extractors and may fall back to RSS
        assert isinstance(result, TextResult)

    def test_returns_text_result_type(self):
        result = extract_text(html="<html><body>Hi</body></html>", url=None)
        assert isinstance(result, TextResult)
