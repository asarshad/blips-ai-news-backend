"""Tests for app.extraction.next_data — __NEXT_DATA__ JSON extraction."""

from __future__ import annotations

import json

from app.extraction.next_data import (
    _domain_root,
    _walk,
    extract_next_data,
    extract_text_from_next_data,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_html(data: dict) -> str:
    """Wrap *data* in a minimal Next.js-style HTML page."""
    blob = json.dumps(data)
    return (
        f"<!DOCTYPE html><html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'
        f'</head><body><div id="__next"></div></body></html>'
    )


# ── extract_next_data ─────────────────────────────────────────────────────────


class TestExtractNextData:
    def test_returns_dict_for_valid_tag(self):
        data = {"props": {"pageProps": {"article": {"body": "hello"}}}}
        html = _make_html(data)
        result = extract_next_data(html)
        assert result == data

    def test_returns_none_when_tag_absent(self):
        assert extract_next_data("<html><body></body></html>") is None

    def test_returns_none_on_malformed_json(self):
        html = '<script id="__NEXT_DATA__">{bad json</script>'
        assert extract_next_data(html) is None

    def test_handles_double_and_single_quotes_in_id(self):
        blob = json.dumps({"x": 1})
        html = f"<script id='__NEXT_DATA__'>{blob}</script>"
        assert extract_next_data(html) == {"x": 1}


# ── _walk ─────────────────────────────────────────────────────────────────────


class TestWalk:
    def test_simple_nested_keys(self):
        data = {"a": {"b": {"c": "value"}}}
        assert _walk(data, ["a", "b", "c"]) == "value"

    def test_array_index_notation(self):
        data = {"items": [{"text": "first"}, {"text": "second"}]}
        assert _walk(data, ["items[1]"]) == {"text": "second"}

    def test_missing_key_returns_none(self):
        assert _walk({"a": 1}, ["b"]) is None

    def test_out_of_bounds_index_returns_none(self):
        data = {"items": [{"x": 1}]}
        assert _walk(data, ["items[5]"]) is None

    def test_index_on_non_dict_returns_none(self):
        data = {"a": "not_a_dict"}
        assert _walk(data, ["a", "b"]) is None

    def test_none_data_returns_none(self):
        assert _walk(None, ["a"]) is None


# ── _domain_root ──────────────────────────────────────────────────────────────


class TestDomainRoot:
    def test_strips_www_subdomain(self):
        assert _domain_root("https://www.cnet.com/article/test") == "cnet.com"

    def test_bare_domain(self):
        assert _domain_root("https://engadget.com/story") == "engadget.com"

    def test_deep_subdomain(self):
        assert _domain_root("https://m.theverge.com/path") == "theverge.com"

    def test_empty_url(self):
        # Should not raise; returns empty string
        assert _domain_root("") == ""


# ── extract_text_from_next_data ───────────────────────────────────────────────


class TestExtractTextFromNextData:
    def test_returns_none_for_non_nextjs_page(self):
        html = "<html><body><article>Content</article></body></html>"
        assert extract_text_from_next_data(html, "https://example.com") is None

    def test_extracts_plain_text_body_via_known_domain_path(self):
        article_text = "This is the full article body with enough words to pass quality gates."
        data = {"props": {"pageProps": {"article": {"body": article_text}}}}
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.cnet.com/article/test")
        assert result is not None
        assert "full article body" in result

    def test_extracts_html_body_and_strips_tags(self):
        html_body = "<p>First paragraph.</p><p>Second paragraph.</p>"
        data = {"props": {"pageProps": {"article": {"body": html_body}}}}
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.engadget.com/story")
        assert result is not None
        assert "<p>" not in result
        assert "First paragraph" in result

    def test_falls_through_to_generic_paths_for_unknown_domain(self):
        article_text = "Generic article content found at a standard path."
        data = {"props": {"pageProps": {"article": {"body": article_text}}}}
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.unknown-spa-site.com/article")
        assert result is not None
        assert "Generic article content" in result

    def test_returns_none_when_all_paths_miss(self):
        data = {"props": {"pageProps": {"somethingElse": {"weirdKey": "text"}}}}
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.cnet.com/article")
        assert result is None

    def test_theverge_array_index_path(self):
        body_text = "The Verge article content goes here."
        data = {
            "props": {
                "pageProps": {
                    "hydration": {"responses": [{"data": {"article": {"body": body_text}}}]}
                }
            }
        }
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.theverge.com/article/1234")
        assert result is not None
        assert "The Verge article content" in result

    def test_returns_none_for_empty_body_field(self):
        data = {"props": {"pageProps": {"article": {"body": ""}}}}
        html = _make_html(data)
        result = extract_text_from_next_data(html, "https://www.cnet.com/article")
        assert result is None


# ── Integration: extract_with_next_data in cascade ───────────────────────────


class TestExtractWithNextDataCascade:
    """Verify extract_with_next_data is exposed and returns expected TextResult."""

    def test_import_and_returns_text_result(self):
        from app.extraction.text_extract import TextResult, extract_with_next_data

        result = extract_with_next_data("", url=None)
        assert isinstance(result, TextResult)
        assert result.extractor == "next_data"

    def test_returns_good_result_for_next_data_page(self):
        from app.extraction.text_extract import extract_with_next_data

        long_text = ("The article discusses important developments in technology. " * 20).strip()
        data = {"props": {"pageProps": {"article": {"body": long_text}}}}
        html = _make_html(data)
        result = extract_with_next_data(html, url="https://www.cnet.com/article/foo")
        assert result.extractor == "next_data"
        assert result.word_count > 0
