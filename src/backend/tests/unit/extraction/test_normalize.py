"""Tests for app.extraction.normalize — URL validation, text cleanup, quality scoring."""

import pytest

from app.extraction.normalize import (
    clean_text,
    compute_text_quality_score,
    is_good_text,
    is_suspicious_image_url,
    make_absolute_url,
    validate_image_url,
    word_count,
)

# ═══════════════════════════════════════════════════════════════════════════════
# validate_image_url
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateImageUrl:
    """validate_image_url returns valid absolute https/http URLs or None."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://example.com/image.jpg", "https://example.com/image.jpg"),
            ("http://cdn.example.com/pic.png", "http://cdn.example.com/pic.png"),
            ("https://cdn.example.com/img?w=600&h=400", "https://cdn.example.com/img?w=600&h=400"),
        ],
    )
    def test_valid_urls(self, url, expected):
        assert validate_image_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "   ",
            "data:image/png;base64,abc",
            "blob:https://example.com/uuid",
            "javascript:void(0)",
            "about:blank",
            "/relative/path/image.jpg",
            "relative/path.jpg",
            "ftp://example.com/image.jpg",
            "file:///etc/passwd",
        ],
    )
    def test_invalid_urls_return_none(self, url):
        assert validate_image_url(url) is None

    def test_whitespace_stripped(self):
        assert (
            validate_image_url("  https://example.com/img.jpg  ") == "https://example.com/img.jpg"
        )

    def test_tracker_beacon_with_collect_path_rejected(self):
        url = "https://metrics.example.com/g/collect?tid=G-TEST&cid=123&en=page_view"
        assert validate_image_url(url) is None

    def test_nextjs_image_proxy_url_unwrapped(self):
        # VentureBeat (and other Next.js sites) put the /_next/image proxy URL
        # in og:image. validate_image_url should unwrap it to the inner CDN URL.
        inner = "https://images.ctfassets.net/abc/def/photo.png"
        from urllib.parse import quote

        proxy = f"https://venturebeat.com/_next/image?url={quote(inner)}&w=3840&q=85"
        assert validate_image_url(proxy) == inner

    def test_nextjs_image_proxy_inner_url_with_query_params(self):
        # Inner URL may itself carry query params (w=, q= on the ctfassets URL).
        from urllib.parse import quote

        inner = "https://images.ctfassets.net/abc/img.png?w=1000&q=100"
        proxy = f"https://example.com/_next/image?url={quote(inner)}&w=800&q=75"
        assert validate_image_url(proxy) == inner

    def test_non_nextjs_image_url_unchanged(self):
        url = "https://cdn.example.com/image.jpg"
        assert validate_image_url(url) == url


class TestSuspiciousImageUrl:
    def test_tracker_host_flagged(self):
        assert is_suspicious_image_url("https://www.google-analytics.com/g/collect?tid=G-TEST")

    def test_query_heavy_non_image_path_flagged(self):
        assert is_suspicious_image_url(
            "https://cdn.example.com/metrics?utm_source=feed&event_name=view"
        )

    def test_normal_editorial_image_not_flagged(self):
        assert not is_suspicious_image_url(
            "https://platform.theverge.com/wp-content/uploads/sites/2/2026/03/topical-dancer.jpg?w=1200"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# make_absolute_url
# ═══════════════════════════════════════════════════════════════════════════════


class TestMakeAbsoluteUrl:
    """make_absolute_url resolves relative URLs against a base."""

    def test_already_absolute(self):
        assert (
            make_absolute_url("https://cdn.example.com/img.jpg", "https://example.com")
            == "https://cdn.example.com/img.jpg"
        )

    def test_relative_path(self):
        assert (
            make_absolute_url("/images/hero.jpg", "https://example.com/article/1")
            == "https://example.com/images/hero.jpg"
        )

    def test_relative_no_slash(self):
        result = make_absolute_url("hero.jpg", "https://example.com/article/1")
        assert result == "https://example.com/article/hero.jpg"

    def test_protocol_relative(self):
        assert (
            make_absolute_url("//cdn.example.com/img.jpg", "https://example.com")
            == "https://cdn.example.com/img.jpg"
        )

    @pytest.mark.parametrize("url", [None, "", "   "])
    def test_empty_returns_none(self, url):
        assert make_absolute_url(url, "https://example.com") is None


# ═══════════════════════════════════════════════════════════════════════════════
# clean_text
# ═══════════════════════════════════════════════════════════════════════════════


class TestCleanText:
    """clean_text strips boilerplate and normalizes whitespace."""

    def test_none_returns_empty(self):
        assert clean_text(None) == ""

    def test_empty_returns_empty(self):
        assert clean_text("") == ""

    def test_strips_whitespace(self):
        assert clean_text("  hello world  ") == "hello world"

    def test_collapses_newlines(self):
        result = clean_text("para1\n\n\n\n\npara2")
        assert result == "para1\n\npara2"

    def test_strips_newsletter_boilerplate(self):
        text = "Great article content here.\nSubscribe to our newsletter for updates.\nMore real content."
        result = clean_text(text)
        assert "Subscribe to our newsletter" not in result
        assert "Great article content here." in result
        assert "More real content." in result

    def test_strips_cookie_boilerplate(self):
        text = "Main article text.\nAccept cookies to continue.\nAnother paragraph."
        result = clean_text(text)
        assert "Accept cookies" not in result

    def test_strips_copyright(self):
        text = "Article body.\n© 2024 All rights reserved."
        result = clean_text(text)
        assert "All rights reserved" not in result

    def test_preserves_real_content(self):
        text = "This is a real article about Python programming.\nIt covers async patterns and FastAPI."
        assert clean_text(text) == text


# ═══════════════════════════════════════════════════════════════════════════════
# word_count / is_good_text / compute_text_quality_score
# ═══════════════════════════════════════════════════════════════════════════════


class TestWordCount:
    def test_empty(self):
        assert word_count("") == 0
        assert word_count(None) == 0

    def test_basic(self):
        assert word_count("hello world foo") == 3


class TestIsGoodText:
    """is_good_text returns True for substantial, quality content."""

    def test_none_is_not_good(self):
        assert is_good_text(None) is False

    def test_empty_is_not_good(self):
        assert is_good_text("") is False

    def test_short_is_not_good(self):
        assert is_good_text("This is a short sentence.") is False

    def test_enough_words_is_good(self):
        words = " ".join(["word"] * 150)
        assert is_good_text(words) is True

    def test_low_alnum_ratio_is_bad(self):
        # Text with lots of punctuation/symbols
        text = "!!! ??? $$$ *** " * 30
        assert is_good_text(text) is False

    def test_custom_min_words(self):
        words = " ".join(["hello"] * 50)
        assert is_good_text(words, min_words=30) is True
        assert is_good_text(words, min_words=100) is False


class TestTextQualityScore:
    """compute_text_quality_score returns 0.0–1.0."""

    def test_none_returns_zero(self):
        assert compute_text_quality_score(None) == 0.0

    def test_empty_returns_zero(self):
        assert compute_text_quality_score("") == 0.0

    def test_good_text_high_score(self):
        words = " ".join(["technology"] * 400)
        score = compute_text_quality_score(words)
        assert score >= 0.7

    def test_short_text_lower_score(self):
        words = " ".join(["hello"] * 50)
        score = compute_text_quality_score(words)
        assert 0.0 < score < 0.8

    def test_score_in_range(self):
        text = "Python is great for building web APIs with FastAPI. " * 20
        score = compute_text_quality_score(text)
        assert 0.0 <= score <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# validate_image_url — SSRF protection
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateImageUrlSsrf:
    """validate_image_url must reject private/loopback/link-local image hosts."""

    def test_localhost_rejected(self):
        # localhost resolves to 127.0.0.1 (loopback)
        assert validate_image_url("http://localhost/image.jpg") is None

    def test_loopback_ip_rejected(self):
        assert validate_image_url("http://127.0.0.1/image.png") is None

    def test_private_class_c_rejected(self):
        assert validate_image_url("http://192.168.1.1/photo.jpg") is None

    def test_private_class_a_rejected(self):
        assert validate_image_url("http://10.0.0.1/img.png") is None

    def test_link_local_metadata_service_rejected(self):
        # AWS/GCP metadata endpoint
        assert validate_image_url("http://169.254.169.254/image") is None

    def test_public_ip_allowed(self):
        # Standard CDN-style URL should still pass
        result = validate_image_url("https://cdn.example.com/image.jpg")
        assert result == "https://cdn.example.com/image.jpg"


# ═══════════════════════════════════════════════════════════════════════════════
# clean_text — boilerplate word-count guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestBoilerplateWordCountGuard:
    """Short boilerplate lines are stripped; editorial sentences that happen to
    contain the phrase are preserved."""

    def test_short_privacy_policy_line_stripped(self):
        text = "Privacy Policy\nSome real article content here."
        cleaned = clean_text(text)
        assert "Privacy Policy" not in cleaned
        assert "real article content" in cleaned

    def test_editorial_mention_preserved(self):
        # A long sentence mentioning "privacy policy" in context should NOT be stripped
        editorial = "The company updated its privacy policy to reflect new data regulations."
        cleaned = clean_text(editorial)
        assert "privacy policy" in cleaned.lower()

    def test_short_terms_line_stripped(self):
        text = "Terms of Service\nActual article text."
        cleaned = clean_text(text)
        assert "Terms of Service" not in cleaned
        assert "Actual article text" in cleaned

    def test_editorial_terms_preserved(self):
        editorial = "Users must agree to the terms of service before accessing premium content."
        cleaned = clean_text(editorial)
        assert "terms of service" in cleaned.lower()
