"""
Regression tests for the two production incidents:

1. Articles "Content Not Found" — ai_processed=True requirement with
   summarization disabled in production.
2. Reels quality — non-short content served on the reels endpoint.

Every test here is tied to a specific root cause that was discovered
during incident response.  If these tests break, the incidents are back.
"""

from datetime import datetime

# ---------------------------------------------------------------------------
# Incident 1 — Articles ai_processed gate
# ---------------------------------------------------------------------------


class TestArticlesFeedAiProcessed:
    """
    POLICY: Summarization is enabled in production.  Articles without
    AI summaries should not be shown to users.  The user-facing articles
    feed must filter to ai_processed=True so only summarized content appears.
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

    def test_article_serializer_hides_pending_url_titles(self):
        from app.api.routes.articles import _content_item_to_article_schema

        item = type(
            "Item",
            (),
            {
                "id": 1,
                "title": "[pending] https://bloomberg.com/news/articles/2026-03-21/openai-plans",
                "canonical_url": None,
                "source_url": "https://bloomberg.com/news/articles/2026-03-21/openai-plans",
                "summary": "OpenAI plans to hire more people.",
                "image_url": None,
                "published_at": datetime(2026, 3, 22, 0, 0, 0),
                "created_at": datetime(2026, 3, 22, 0, 0, 0),
                "topics": ["openai"],
            },
        )()

        payload = _content_item_to_article_schema(item)

        assert payload["title"] == "OpenAI Plans"


# ---------------------------------------------------------------------------
# Incident 1b — Videos feed same risk
# ---------------------------------------------------------------------------


class TestVideosFeedAiProcessed:
    """Videos must surface immediately after promotion; AI enrichment is asynchronous."""

    def test_videos_recent_does_not_wait_for_ai_processed(self):
        import inspect

        from app.api.routes.videos import get_recent_videos

        source = inspect.getsource(get_recent_videos)
        assert "require_ai_processed=False" in source

    def test_videos_returns_cursor_envelope_instead_of_404(self):
        import inspect

        from app.api.routes.videos import get_recent_videos

        source = inspect.getsource(get_recent_videos)
        header_pos = source.index("add_headers")
        return_pos = source.index("return {")
        assert header_pos < return_pos
        assert "raise HTTPException(status_code=404" not in source
        assert '"inventory_state": inventory_state' in source


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
    ROOT CAUSE: Ingestion treated short duration as sufficient evidence for REEL,
    which misclassified plain `watch?v=` videos under 3 minutes.

    FIX: Only durable Shorts signals should create REELs.
    """

    def test_duration_only_no_longer_promotes_reels(self):
        """Short duration alone must not be enough to classify a reel."""
        from app.models.content import ContentType
        from app.video_surface_rules import classify_video_like_item

        item = type(
            "Item",
            (),
            {
                "duration_seconds": 134,
                "video_url": "https://www.youtube.com/watch?v=watch123",
                "title": "Foundry IQ: Building the Data Pipeline with Knowledge Sources",
                "channel_id": "UC9PBzalIcEQCsiIkq36PyUA",
                "content_format": None,
            },
        )()

        assert classify_video_like_item(item) == ContentType.VIDEO, (
            "Short duration alone must not reclassify a normal watch URL as a reel"
        )

    def test_duration_authority_still_protects_long_videos(self):
        """Known long duration must still force VIDEO even with reel-like signals."""
        from app.models.content import ContentType
        from app.video_surface_rules import classify_video_like_item

        item = type(
            "Item",
            (),
            {
                "duration_seconds": 601,
                "video_url": "https://www.youtube.com/shorts/not-actually-short",
                "title": "#shorts but long",
                "channel_id": None,
            },
        )()

        assert classify_video_like_item(item) == ContentType.VIDEO, (
            "Long duration must still override reel-like hints"
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
