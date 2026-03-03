"""
Regression tests for the two production incidents:

1. Articles "Content Not Found" — ai_processed=True requirement with
   summarization disabled in production.
2. Reels quality — non-short content served on the reels endpoint.

Every test here is tied to a specific root cause that was discovered
during incident response.  If these tests break, the incidents are back.
"""


# ---------------------------------------------------------------------------
# Incident 1 — Articles ai_processed gate
# ---------------------------------------------------------------------------


class TestArticlesFeedAiProcessed:
    """
    POLICY: Summarization is enabled in production.  Articles without
    AI summaries should not be shown to users.  All article endpoints
    must filter to ai_processed=True so only summarized content appears.
    """

    def test_articles_recent_requires_ai_processed(self):
        """
        The /articles/recent endpoint must filter on ai_processed=True
        so only articles with AI summaries are shown.
        """
        import inspect

        from app.api.routes.articles import get_recent_articles

        source = inspect.getsource(get_recent_articles)
        assert "require_ai_processed=True" in source, (
            "get_recent_articles must use require_ai_processed=True "
            "to hide articles without AI summaries"
        )

    def test_articles_next_requires_ai_processed(self):
        """The /articles/next endpoint must filter on ai_processed."""
        import inspect

        from app.api.routes.articles import get_next_article

        source = inspect.getsource(get_next_article)
        assert "ai_processed_only=True" in source

    def test_articles_cache_requires_ai_processed(self):
        """The /articles/cache endpoint must filter on ai_processed."""
        import inspect

        from app.api.routes.articles import get_cached_articles

        source = inspect.getsource(get_cached_articles)
        assert "ai_processed_only=True" in source


class TestArticlesDiagnosticHeaders:
    """
    The old code raised HTTPException(404) BEFORE setting diagnostic
    response headers, making the 404 impossible to diagnose remotely.

    FIX: headers are set before the empty-check / 404.
    """

    def test_headers_before_404(self):
        """
        In get_recent_articles source, FeedMetadata.add_headers must appear
        BEFORE the `raise HTTPException(status_code=404` line.
        """
        import inspect

        from app.api.routes.articles import get_recent_articles

        source = inspect.getsource(get_recent_articles)
        header_pos = source.index("add_headers")
        raise_pos = source.index("raise HTTPException(status_code=404")
        assert header_pos < raise_pos, (
            "Diagnostic headers must be set BEFORE the 404 is raised, "
            "otherwise response headers are lost on error"
        )


# ---------------------------------------------------------------------------
# Incident 1b — Videos feed same risk
# ---------------------------------------------------------------------------


class TestVideosFeedAiProcessed:
    """Videos must also require AI processing — only show summarized videos."""

    def test_videos_recent_requires_ai_processed(self):
        import inspect

        from app.api.routes.videos import get_recent_videos

        source = inspect.getsource(get_recent_videos)
        assert "require_ai_processed=True" in source

    def test_videos_headers_before_404(self):
        import inspect

        from app.api.routes.videos import get_recent_videos

        source = inspect.getsource(get_recent_videos)
        header_pos = source.index("add_headers")
        raise_pos = source.index("raise HTTPException(status_code=404")
        assert header_pos < raise_pos


# ---------------------------------------------------------------------------
# Incident 2 — Reels quality (duration enforcement)
# ---------------------------------------------------------------------------


class TestReelsDurationFilter:
    """
    ROOT CAUSE: The reels feed endpoint had NO duration filter.  Items
    classified as REEL during ingestion (via is_short metadata flag or
    /shorts/ URL) were served even if their actual duration was > 3 min.

    FIX: tiered_feed_service.get_tiered_feed adds a duration filter
    when surface == REELS.
    """

    def test_tiered_feed_service_has_reel_duration_guard(self):
        """get_tiered_feed source must include REEL_MAX_DURATION_SECONDS guard."""
        import inspect

        from app.services.tiered_feed_service import get_tiered_feed

        source = inspect.getsource(get_tiered_feed)
        assert "REEL_MAX_DURATION_SECONDS" in source, (
            "get_tiered_feed must enforce REEL_MAX_DURATION_SECONDS "
            "as defense-in-depth for the reels surface"
        )
        assert "Surface.REELS" in source

    def test_reel_max_duration_config_exists(self):
        """The REEL_MAX_DURATION_SECONDS setting must exist and be reasonable."""
        from app.core.config import settings

        assert hasattr(settings, "REEL_MAX_DURATION_SECONDS")
        assert 60 <= settings.REEL_MAX_DURATION_SECONDS <= 300, (
            f"REEL_MAX_DURATION_SECONDS={settings.REEL_MAX_DURATION_SECONDS} "
            "should be between 60 and 300"
        )


class TestReelClassificationIngestion:
    """
    ROOT CAUSE: Ingestion used `if is_short or is_shorts_url or is_short_duration`
    with a logical OR, so is_short metadata could override known long duration.

    FIX: Known duration is authoritative — if duration > max, always VIDEO.
    """

    def test_long_duration_overrides_is_short_flag(self):
        """A video with known long duration must NOT become a REEL."""
        import inspect

        from app.ingestion.service import IngestionPipeline

        source = inspect.getsource(IngestionPipeline.ingest_youtube_entry)
        # The new code should check is_long_duration FIRST
        assert "is_long_duration" in source, (
            "Ingestion must check for long duration and force VIDEO type"
        )

    def test_classification_priority_order(self):
        """
        In the new ingestion code, the priority must be:
        1. is_long_duration → VIDEO (authoritative)
        2. is_short_duration → REEL (authoritative)
        3. is_shorts_url → REEL (strong hint)
        4. is_short metadata → REEL (weak hint, only if no duration)
        """
        import inspect

        from app.ingestion.service import IngestionPipeline

        source = inspect.getsource(IngestionPipeline.ingest_youtube_entry)

        # is_long_duration check must come BEFORE is_short_duration
        # After the first mention, find the if-block ordering
        long_if = source.index("if is_long_duration")
        short_elif = source.index("elif is_short_duration")
        url_elif = source.index("elif is_shorts_url")

        assert long_if < short_elif < url_elif, (
            "Classification must check long_duration first, then short_duration, then URL pattern"
        )


# ---------------------------------------------------------------------------
# Reels endpoint still uses require_ai_processed=False (no regression)
# ---------------------------------------------------------------------------


class TestReelsEndpointConfig:
    """Ensure the reels endpoint does not regress to requiring ai_processed."""

    def test_reels_endpoint_does_not_require_ai(self):
        import inspect

        from app.api.routes.videos import get_reels

        source = inspect.getsource(get_reels)
        assert "require_ai_processed=False" in source


# ---------------------------------------------------------------------------
# Debug inventory endpoint exists
# ---------------------------------------------------------------------------


class TestDebugInventoryEndpoint:
    """The /debug/inventory endpoint must exist for future incident response."""

    def test_debug_inventory_endpoint_exists(self):
        from app.api.routes.debug import get_inventory_breakdown

        assert callable(get_inventory_breakdown)

    def test_debug_inventory_returns_surfaces(self):
        """The inventory endpoint must return per-content-type breakdowns."""
        import inspect

        from app.api.routes.debug import get_inventory_breakdown

        source = inspect.getsource(get_inventory_breakdown)
        assert "ai_processed_true" in source
        assert "ai_processed_false" in source
        assert "misclassified_candidates" in source
        assert "duration" in source
