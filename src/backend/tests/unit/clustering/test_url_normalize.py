"""Unit tests for canonical_cluster_url (P2-5)."""

from __future__ import annotations

from app.clustering.url_normalize import canonical_cluster_url


class TestCanonicalClusterUrl:
    # ------------------------------------------------------------------
    # Basic stripping
    # ------------------------------------------------------------------

    def test_strips_utm_params(self):
        url = "https://techcrunch.com/2024/01/01/story/?utm_source=rss&utm_medium=feed"
        assert canonical_cluster_url(url) == "techcrunch.com/2024/01/01/story"

    def test_strips_all_query_params(self):
        url = "https://example.com/article?ref=newsletter&foo=bar"
        assert canonical_cluster_url(url) == "example.com/article"

    def test_strips_fragment(self):
        url = "https://example.com/article#section-2"
        assert canonical_cluster_url(url) == "example.com/article"

    def test_strips_query_and_fragment(self):
        url = "https://example.com/article?foo=1#top"
        assert canonical_cluster_url(url) == "example.com/article"

    # ------------------------------------------------------------------
    # www / amp prefix stripping
    # ------------------------------------------------------------------

    def test_strips_www_prefix(self):
        url = "https://www.techcrunch.com/2024/01/01/story"
        assert canonical_cluster_url(url) == "techcrunch.com/2024/01/01/story"

    def test_strips_amp_subdomain(self):
        url = "https://amp.theverge.com/2024/01/01/story"
        assert canonical_cluster_url(url) == "theverge.com/2024/01/01/story"

    # ------------------------------------------------------------------
    # AMP path suffix stripping
    # ------------------------------------------------------------------

    def test_strips_amp_path_suffix(self):
        url = "https://arstechnica.com/science/2024/01/story/amp"
        assert canonical_cluster_url(url) == "arstechnica.com/science/2024/01/story"

    def test_strips_amp_path_suffix_with_trailing_slash(self):
        url = "https://arstechnica.com/science/2024/01/story/amp/"
        assert canonical_cluster_url(url) == "arstechnica.com/science/2024/01/story"

    # ------------------------------------------------------------------
    # Trailing slash handling
    # ------------------------------------------------------------------

    def test_strips_trailing_slash_on_path(self):
        url = "https://example.com/article/"
        assert canonical_cluster_url(url) == "example.com/article"

    def test_preserves_root_path(self):
        url = "https://example.com/"
        assert canonical_cluster_url(url) == "example.com/"

    def test_no_trailing_slash_unchanged(self):
        url = "https://example.com/article"
        assert canonical_cluster_url(url) == "example.com/article"

    # ------------------------------------------------------------------
    # None / empty inputs
    # ------------------------------------------------------------------

    def test_none_returns_none(self):
        assert canonical_cluster_url(None) is None

    def test_empty_string_returns_none(self):
        assert canonical_cluster_url("") is None

    # ------------------------------------------------------------------
    # Scheme-independence (same result for http and https)
    # ------------------------------------------------------------------

    def test_http_and_https_produce_same_key(self):
        http = canonical_cluster_url("http://techcrunch.com/2024/01/story")
        https = canonical_cluster_url("https://techcrunch.com/2024/01/story")
        assert http == https == "techcrunch.com/2024/01/story"

    # ------------------------------------------------------------------
    # Two URLs that should collide
    # ------------------------------------------------------------------

    def test_rss_and_social_urls_collide(self):
        """Same article path, different tracking params → same key."""
        rss = canonical_cluster_url("https://www.theverge.com/2024/1/1/ai-article?utm_source=rss")
        social = canonical_cluster_url(
            "https://theverge.com/2024/1/1/ai-article?ref=twitter&fbclid=abc123"
        )
        assert rss == social

    def test_amp_and_canonical_urls_collide(self):
        """AMP subdomain variant and canonical form → same key."""
        amp = canonical_cluster_url("https://amp.arstechnica.com/tech-policy/2024/01/story/")
        canonical = canonical_cluster_url("https://arstechnica.com/tech-policy/2024/01/story")
        assert amp == canonical

    # ------------------------------------------------------------------
    # Two URLs that should NOT collide
    # ------------------------------------------------------------------

    def test_different_paths_do_not_collide(self):
        a = canonical_cluster_url("https://techcrunch.com/2024/01/story-one")
        b = canonical_cluster_url("https://techcrunch.com/2024/01/story-two")
        assert a != b

    def test_different_hosts_do_not_collide(self):
        a = canonical_cluster_url("https://techcrunch.com/2024/01/story")
        b = canonical_cluster_url("https://theverge.com/2024/01/story")
        assert a != b

    # ------------------------------------------------------------------
    # Case insensitivity on host
    # ------------------------------------------------------------------

    def test_host_is_lowercased(self):
        url = "https://TechCrunch.COM/2024/01/story"
        assert canonical_cluster_url(url) == "techcrunch.com/2024/01/story"
