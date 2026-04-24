"""Unit tests for the X (Twitter) signal fetcher.

Tests cover:
- URL safety validation (_is_safe_content_url)
- SSRF protection (_is_private_host)
- URL extraction from tweet payloads (_extract_urls_from_tweet)
- Score calculation (compute_x_signal_score)
- Cohort/topical query building
- Full fetch_x_signals with mocked API
- Feature flag / missing token guard behavior
- Integration: article URL from X enters pipeline correctly
- Integration: YouTube watch URL accepted, channel URL rejected
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.signals.x_signal_fetcher import (
    _build_cohort_queries,
    _build_topical_queries,
    _extract_urls_from_tweet,
    _is_private_host,
    _is_safe_content_url,
    _tweet_age_hours,
    compute_x_signal_score,
    fetch_x_signals,
)
from app.models.signal import SignalSource


# ── _is_private_host ──────────────────────────────────────────────────────────


class TestIsPrivateHost:
    def test_localhost(self):
        assert _is_private_host("localhost") is True

    def test_127_loopback(self):
        assert _is_private_host("127.0.0.1") is True

    def test_private_10_range(self):
        assert _is_private_host("10.0.0.1") is True

    def test_private_192_168(self):
        assert _is_private_host("192.168.1.1") is True

    def test_private_172_range(self):
        assert _is_private_host("172.20.0.1") is True

    def test_link_local(self):
        assert _is_private_host("169.254.169.254") is True  # AWS/GCP metadata

    def test_local_suffix(self):
        assert _is_private_host("mybox.local") is True

    def test_internal_suffix(self):
        assert _is_private_host("db.internal") is True

    def test_public_ip(self):
        assert _is_private_host("8.8.8.8") is False

    def test_public_hostname(self):
        assert _is_private_host("techcrunch.com") is False

    def test_ipv6_loopback(self):
        assert _is_private_host("::1") is True


# ── _is_safe_content_url ──────────────────────────────────────────────────────


class TestIsSafeContentUrl:
    def test_valid_article_url(self):
        assert _is_safe_content_url("https://techcrunch.com/2026/04/01/ai-story/") is True

    def test_valid_youtube_watch(self):
        assert _is_safe_content_url("https://www.youtube.com/watch?v=abc123") is True

    def test_rejects_twitter_self_link(self):
        assert _is_safe_content_url("https://twitter.com/openai/status/123") is False

    def test_rejects_x_com_self_link(self):
        assert _is_safe_content_url("https://x.com/sama/status/456") is False

    def test_rejects_t_co_unresolved(self):
        assert _is_safe_content_url("https://t.co/XYZ123") is False

    def test_rejects_twimg_media(self):
        assert _is_safe_content_url("https://pbs.twimg.com/media/abc.jpg") is False

    def test_rejects_homepage_root(self):
        assert _is_safe_content_url("https://techcrunch.com/") is False

    def test_rejects_homepage_empty_path(self):
        assert _is_safe_content_url("https://techcrunch.com") is False

    def test_rejects_youtube_channel(self):
        assert _is_safe_content_url("https://www.youtube.com/channel/UCxyz") is False

    def test_rejects_youtube_handle_page(self):
        assert _is_safe_content_url("https://www.youtube.com/@OpenAI") is False

    def test_rejects_youtube_c_page(self):
        assert _is_safe_content_url("https://www.youtube.com/c/GoogleDeepMind") is False

    def test_rejects_youtube_user_page(self):
        assert _is_safe_content_url("https://www.youtube.com/user/TechChannel") is False

    def test_rejects_tag_page(self):
        assert _is_safe_content_url("https://arstechnica.com/tag/ai/") is False

    def test_rejects_non_https_scheme(self):
        assert _is_safe_content_url("ftp://files.example.com/file.txt") is False

    def test_rejects_private_ip(self):
        assert _is_safe_content_url("http://192.168.1.1/admin") is False

    def test_rejects_localhost(self):
        assert _is_safe_content_url("http://localhost:8080/api") is False

    def test_rejects_empty_url(self):
        assert _is_safe_content_url("") is False

    def test_rejects_metadata_endpoint(self):
        assert _is_safe_content_url("http://169.254.169.254/latest/meta-data/") is False

    def test_accepts_arxiv_paper(self):
        assert _is_safe_content_url("https://arxiv.org/abs/2304.01234") is True

    def test_accepts_github_repo(self):
        assert _is_safe_content_url("https://github.com/openai/gpt-4") is True

    def test_accepts_youtube_shorts(self):
        assert _is_safe_content_url("https://www.youtube.com/shorts/vidid123") is True

    def test_accepts_substack_post(self):
        assert _is_safe_content_url("https://example.substack.com/p/my-post") is True

    def test_rejects_search_page(self):
        assert _is_safe_content_url("https://arstechnica.com/search?q=ai") is False


# ── _extract_urls_from_tweet ──────────────────────────────────────────────────


class TestExtractUrlsFromTweet:
    def _make_tweet(self, urls: list[str]) -> dict:
        return {
            "id": "12345",
            "entities": {
                "urls": [
                    {"url": f"https://t.co/X{i}", "expanded_url": u}
                    for i, u in enumerate(urls)
                ]
            },
        }

    def test_extracts_expanded_urls(self):
        tweet = self._make_tweet(["https://techcrunch.com/article/", "https://youtube.com/watch?v=abc"])
        result = _extract_urls_from_tweet(tweet)
        assert "https://techcrunch.com/article/" in result
        assert "https://youtube.com/watch?v=abc" in result

    def test_empty_entities(self):
        assert _extract_urls_from_tweet({"id": "1"}) == []

    def test_missing_entities_key(self):
        assert _extract_urls_from_tweet({"id": "1", "entities": {}}) == []

    def test_falls_back_to_url_field(self):
        tweet = {
            "id": "1",
            "entities": {
                "urls": [{"url": "https://t.co/ABC", "expanded_url": ""}]
            },
        }
        result = _extract_urls_from_tweet(tweet)
        # Falls back to raw t.co url when expanded_url is empty
        assert "https://t.co/ABC" in result

    def test_multiple_urls_in_single_tweet(self):
        tweet = self._make_tweet(
            [
                "https://openai.com/blog/gpt-5",
                "https://arstechnica.com/story",
                "https://example.com/another",
            ]
        )
        result = _extract_urls_from_tweet(tweet)
        assert len(result) == 3


# ── compute_x_signal_score ────────────────────────────────────────────────────


class TestComputeXSignalScore:
    def test_cohort_fresh_engaged_scores_high(self):
        score = compute_x_signal_score(
            in_cohort=True,
            like_count=500,
            retweet_count=200,
            age_hours=0.5,
        )
        assert score >= 50

    def test_query_old_unengaged_scores_low(self):
        score = compute_x_signal_score(
            in_cohort=False,
            like_count=0,
            retweet_count=0,
            age_hours=20.0,
        )
        assert score <= 20

    def test_cohort_higher_than_query_same_engagement(self):
        cohort = compute_x_signal_score(
            in_cohort=True, like_count=10, retweet_count=5, age_hours=2.0
        )
        query = compute_x_signal_score(
            in_cohort=False, like_count=10, retweet_count=5, age_hours=2.0
        )
        assert cohort > query

    def test_fresh_higher_than_old(self):
        fresh = compute_x_signal_score(
            in_cohort=True, like_count=10, retweet_count=5, age_hours=1.0
        )
        old = compute_x_signal_score(
            in_cohort=True, like_count=10, retweet_count=5, age_hours=15.0
        )
        assert fresh > old

    def test_score_never_below_1(self):
        score = compute_x_signal_score(
            in_cohort=False, like_count=0, retweet_count=0, age_hours=100.0
        )
        assert score >= 1

    def test_score_never_above_100(self):
        score = compute_x_signal_score(
            in_cohort=True, like_count=1_000_000, retweet_count=500_000, age_hours=0.0
        )
        assert score <= 100

    def test_engagement_capped(self):
        score_moderate = compute_x_signal_score(
            in_cohort=False, like_count=1000, retweet_count=500, age_hours=1.0
        )
        score_extreme = compute_x_signal_score(
            in_cohort=False, like_count=10_000_000, retweet_count=5_000_000, age_hours=1.0
        )
        # Extreme engagement should not produce vastly higher score due to cap.
        assert score_extreme - score_moderate <= 5


# ── _build_cohort_queries ─────────────────────────────────────────────────────


class TestBuildCohortQueries:
    def test_small_cohort_single_query(self):
        queries = _build_cohort_queries(["OpenAI", "AnthropicAI"])
        assert len(queries) == 1
        assert "from:OpenAI" in queries[0]
        assert "from:AnthropicAI" in queries[0]
        assert "has:links" in queries[0]

    def test_large_cohort_splits_into_multiple_queries(self):
        accounts = [f"Account{i}" for i in range(30)]
        queries = _build_cohort_queries(accounts)
        assert len(queries) == 2  # 30 accounts, max 15 per query

    def test_query_includes_base_filters(self):
        queries = _build_cohort_queries(["OpenAI"])
        assert "lang:en" in queries[0]
        assert "-is:retweet" in queries[0]


# ── _build_topical_queries ────────────────────────────────────────────────────


class TestBuildTopicalQueries:
    def test_each_term_gets_own_query(self):
        terms = ["AI release", "chip launch"]
        queries = _build_topical_queries(terms)
        assert len(queries) == 2
        assert "AI release" in queries[0]
        assert "chip launch" in queries[1]

    def test_base_filters_appended(self):
        queries = _build_topical_queries(["AI"])
        assert "has:links" in queries[0]
        assert "lang:en" in queries[0]


# ── fetch_x_signals — integration tests with mocked API ──────────────────────


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string so tests don't age out."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_mock_tweet(
    url: str,
    like_count: int = 100,
    retweet_count: int = 20,
    created_at: str | None = None,
) -> dict:
    if created_at is None:
        created_at = _now_iso()
    return {
        "id": "999",
        "created_at": created_at,
        "author_id": "1234",
        "public_metrics": {
            "like_count": like_count,
            "retweet_count": retweet_count,
            "reply_count": 5,
            "quote_count": 2,
        },
        "entities": {
            "urls": [{"url": "https://t.co/X", "expanded_url": url}]
        },
    }


class TestFetchXSignals:
    def test_no_bearer_token_returns_empty(self):
        result = fetch_x_signals(bearer_token="", mode="cohort")
        assert result == []

    def test_mode_off_returns_empty(self):
        result = fetch_x_signals(bearer_token="tok", mode="off")
        assert result == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_article_url_returns_signal_item(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://techcrunch.com/2026/04/20/ai-story/")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert len(items) == 1
        assert items[0].signal_source == SignalSource.X_SIGNAL
        assert "techcrunch.com" in items[0].raw_url

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_youtube_watch_url_accepted(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://www.youtube.com/watch?v=abc123def45")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert len(items) == 1
        assert "youtube.com" in items[0].raw_url

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_youtube_channel_url_rejected(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://www.youtube.com/@OpenAI")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert items == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_twitter_self_link_rejected(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://twitter.com/openai/status/12345")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert items == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_homepage_url_rejected(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://techcrunch.com/")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert items == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_private_ip_rejected(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("http://192.168.1.1/secret")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert items == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_duplicate_urls_deduplicated(self, mock_api):
        url = "https://arstechnica.com/ai/2026/04/story/"
        mock_api.return_value = [
            _make_mock_tweet(url),
            _make_mock_tweet(url),
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert len(items) == 1  # deduped within run

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_limit_respected(self, mock_api):
        tweets = [
            _make_mock_tweet(f"https://example.com/article/{i}/")
            for i in range(20)
        ]
        mock_api.return_value = tweets
        items = fetch_x_signals(bearer_token="tok", mode="cohort", limit=5)
        assert len(items) <= 5

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_min_score_filters_low_score(self, mock_api):
        # Query mode, recent-ish tweet, no engagement = score well below 50.
        # max_tweet_age_hours=200 ensures age filtering is not the reason for exclusion.
        mock_api.return_value = [
            _make_mock_tweet(
                "https://arstechnica.com/ai/2026/story/",
                like_count=0,
                retweet_count=0,
                created_at=_now_iso(),
            )
        ]
        items = fetch_x_signals(
            bearer_token="tok",
            mode="query",
            min_score=50,  # Very high threshold
            max_tweet_age_hours=200,
        )
        # Query mode (tier_weight=15) + no engagement + base 5 = ~20, below 50
        assert items == []

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_allowed_domains_filters_other_domains(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://techcrunch.com/story/"),
            _make_mock_tweet("https://wired.com/story/"),
        ]
        items = fetch_x_signals(
            bearer_token="tok",
            mode="cohort",
            allowed_domains={"techcrunch.com"},
        )
        assert len(items) == 1
        assert "techcrunch.com" in items[0].raw_url

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_api_failure_returns_empty_not_raises(self, mock_api):
        mock_api.side_effect = Exception("unexpected error")
        # Should not raise — fetchers must fail gracefully
        try:
            # The exception will be caught by the try/except in fetch_x_signals
            # wrapping _call_recent_search. If it propagates, test fails.
            items = fetch_x_signals(bearer_token="tok", mode="cohort")
        except Exception:
            pytest.fail("fetch_x_signals raised an exception — must fail gracefully")

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_mixed_mode_runs_both_cohort_and_query(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://openai.com/blog/gpt-5/")
        ]
        items = fetch_x_signals(
            bearer_token="tok",
            mode="mixed",
            cohort_accounts=["OpenAI"],
            query_terms=["AI release"],
        )
        # Two queries (1 cohort + 1 topical) but same URL → deduplicated
        assert len(items) == 1
        assert mock_api.call_count == 2

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_signal_item_has_x_signal_source(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://anthropic.com/news/release/")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert items[0].signal_source == SignalSource.X_SIGNAL

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_signal_item_score_is_positive_int(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet("https://anthropic.com/news/release/")
        ]
        items = fetch_x_signals(bearer_token="tok", mode="cohort")
        assert isinstance(items[0].signal_score, int)
        assert items[0].signal_score >= 1

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_noisy_low_quality_accounts_stay_low_score(self, mock_api):
        mock_api.return_value = [
            _make_mock_tweet(
                "https://example.com/clickbait/",
                like_count=50000,
                retweet_count=20000,
                created_at=_now_iso(),
            )
        ]
        items = fetch_x_signals(
            bearer_token="tok",
            mode="query",  # query mode = lower tier weight
            max_tweet_age_hours=200,
        )
        assert len(items) == 1
        # Even with high engagement, query mode (tier_weight=15) caps total score
        assert items[0].signal_score <= 60


# ── signal pipeline integration behavior tests ────────────────────────────────


class TestSignalPipelineIntegration:
    """
    Verify that X signal items integrate cleanly into the existing orchestrator.
    These tests mock at the fetcher layer and verify orchestrator behavior.
    """

    @patch("app.ingestion.signals.x_signal_fetcher._call_recent_search")
    def test_x_disabled_fetcher_never_called(self, mock_api):
        """When x_signals_enabled=False, the X fetcher must not be called."""
        from unittest.mock import MagicMock, patch
        from app.ingestion.signal_ingestion import run_signal_ingestion

        mock_db = MagicMock()
        mock_db.begin_nested.return_value.__enter__ = MagicMock(return_value=None)
        mock_db.begin_nested.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.ingestion.signal_ingestion.fetch_hn_top", return_value=[]), \
             patch("app.ingestion.signal_ingestion.fetch_hn_best", return_value=[]), \
             patch("app.ingestion.signal_ingestion.fetch_github_trending", return_value=[]), \
             patch("app.ingestion.signal_ingestion._fetch_yt_safe", return_value=[]), \
             patch("app.ingestion.signal_ingestion.fetch_x_signals") as mock_x_fetch:

            run_signal_ingestion(
                mock_db,
                discovery_enabled=False,
                x_signals_enabled=False,
            )
            mock_x_fetch.assert_not_called()

    def test_signal_source_label_defined_for_x(self):
        """X_SIGNAL must have a discovered_via label in _SIGNAL_SOURCE_LABELS."""
        from app.ingestion.signal_ingestion import _SIGNAL_SOURCE_LABELS
        from app.models.signal import SignalSource

        assert SignalSource.X_SIGNAL in _SIGNAL_SOURCE_LABELS
        assert _SIGNAL_SOURCE_LABELS[SignalSource.X_SIGNAL] == "signal_x"
