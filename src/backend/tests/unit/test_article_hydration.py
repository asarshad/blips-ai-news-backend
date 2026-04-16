from types import SimpleNamespace
from unittest.mock import MagicMock

from app.article_hydration import ArticleHydrationService, ArticleImageLLMExtractionResult
from app.models.content import ContentType


def test_build_article_stub_sets_normalized_article_defaults():
    hydrator = ArticleHydrationService()

    stub = hydrator.build_article_stub(
        source_url="https://example.com/blog/how-world-bank-manages-hybrid-cloud",
        image_url="https://cdn.example.com/social-share.png",
    )

    assert stub.type == ContentType.ARTICLE
    assert stub.source_url == "https://example.com/blog/how-world-bank-manages-hybrid-cloud"
    assert stub.canonical_url == stub.source_url
    assert stub.canonical_key
    assert stub.title == "[pending] How World Bank Manages Hybrid Cloud"
    assert stub.image_url is None
    assert stub.article_image_status == "PENDING"
    assert stub.article_image_checked_at is None


def test_prepare_rss_article_uses_page_metadata_when_image_missing(monkeypatch):
    hydrator = ArticleHydrationService()
    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            title="Recovered title",
            canonical_url=f"{article_url}/canonical",
            image_url="https://cdn.example.com/hero.jpg",
        ),
    )

    prepared = hydrator.prepare_rss_article(
        source_url="https://example.com/story",
        title="RSS title",
        description="RSS body",
        image_url=None,
        published_at=None,
        include_text=False,
    )

    assert prepared.title == "Recovered title"
    assert prepared.canonical_url == "https://example.com/story/canonical"
    assert prepared.image_url == "https://cdn.example.com/hero.jpg"
    assert prepared.content_text == "RSS body"


def test_prepare_rss_article_prefers_page_metadata_image_over_rss_image(monkeypatch):
    hydrator = ArticleHydrationService()
    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            title="Recovered title",
            canonical_url=f"{article_url}/canonical",
            image_url="https://cdn.example.com/page-hero.jpg",
            image_source="body",
        ),
    )

    prepared = hydrator.prepare_rss_article(
        source_url="https://example.com/story",
        title="RSS title",
        description="RSS body",
        image_url="https://cdn.example.com/rss-image.jpg",
        published_at=None,
        include_text=False,
    )

    assert prepared.title == "Recovered title"
    assert prepared.canonical_url == "https://example.com/story/canonical"
    assert prepared.image_url == "https://cdn.example.com/page-hero.jpg"


def test_prepare_rss_article_prefers_rss_image_over_weak_page_metadata_og(monkeypatch):
    hydrator = ArticleHydrationService()
    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            title="Recovered title",
            canonical_url=f"{article_url}/canonical",
            image_url="https://s.yimg.com/kw/assets/engadget-amp-proposed.png",
            image_source="og",
        ),
    )

    prepared = hydrator.prepare_rss_article(
        source_url="https://example.com/story",
        title="RSS title",
        description="RSS body",
        image_url="https://cdn.example.com/rss-image.jpg",
        published_at=None,
        include_text=False,
    )

    assert prepared.image_url == "https://cdn.example.com/rss-image.jpg"


def test_prepare_rss_article_returns_none_for_blocked_direct_domain():
    hydrator = ArticleHydrationService()

    prepared = hydrator.prepare_rss_article(
        source_url="https://github.com/owner/repo",
        title="Repo title",
        description="RSS body",
        image_url="https://github.com/owner/repo/raw/main/social.png",
        published_at=None,
        include_text=False,
    )

    assert prepared is None


def test_prepare_rss_article_returns_none_for_direct_binary_asset_url():
    hydrator = ArticleHydrationService()

    prepared = hydrator.prepare_rss_article(
        source_url="https://example.com/files/incident-report.pdf",
        title="Incident report",
        description="Binary asset that should not be scraped as an article.",
        image_url=None,
        published_at=None,
        include_text=False,
    )

    assert prepared is None


def test_prepare_rss_article_returns_none_for_generic_menu_listing_url():
    hydrator = ArticleHydrationService()

    prepared = hydrator.prepare_rss_article(
        source_url="https://www.mcdonalds.co.jp/en/menu/burger/",
        title="Burgers | McDonald's",
        description="Menu listing that should not be ingested as an article.",
        image_url=None,
        published_at=None,
        include_text=False,
    )

    assert prepared is None


def test_prepare_rss_article_allows_article_slug_under_generic_section_prefix():
    hydrator = ArticleHydrationService()
    prepared = hydrator.prepare_rss_article(
        source_url="https://example.com/product/launches-new-enterprise-ai-platform",
        title="Launches new enterprise AI platform",
        description="Real article path under a generic section prefix.",
        image_url=None,
        published_at=None,
        include_text=False,
    )

    assert prepared is not None
    assert prepared.source_url == "https://example.com/product/launches-new-enterprise-ai-platform"


def test_refresh_existing_article_metadata_prefers_page_metadata_over_rss_image(monkeypatch):
    hydrator = ArticleHydrationService()
    item = hydrator.build_article_stub(
        source_url="https://example.com/story",
        title="Recovered title",
        image_url="https://cdn.example.com/stale-rss-image.jpg",
    )
    item.canonical_url = "https://example.com/story"

    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            title="Recovered title",
            canonical_url=f"{article_url}/canonical",
            image_url="https://cdn.example.com/page-hero.jpg",
            image_source="body",
        ),
    )

    changed = hydrator.refresh_existing_article_metadata(
        item,
        source_url="https://example.com/story",
        rss_image_url="https://cdn.example.com/rss-image.jpg",
        force_reconcile_image=True,
    )

    assert changed is True
    assert item.image_url == "https://cdn.example.com/page-hero.jpg"
    assert item.canonical_url == "https://example.com/story"


def test_refresh_existing_article_metadata_uses_llm_fallback_when_metadata_has_no_image(
    monkeypatch,
):
    hydrator = ArticleHydrationService()
    item = hydrator.build_article_stub(
        source_url="https://example.com/story",
        title="Recovered title",
        image_url=None,
    )
    item.canonical_url = "https://example.com/story"

    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            canonical_url=article_url,
            image_url=None,
            image_source="none",
        ),
    )
    monkeypatch.setattr(
        hydrator,
        "extract_article_image_with_llm",
        lambda **_kwargs: ArticleImageLLMExtractionResult(
            image_url="https://cdn.example.com/story-hero.jpg",
            reason="validated",
        ),
    )

    changed = hydrator.refresh_existing_article_metadata(
        item,
        source_url="https://example.com/story",
        rss_image_url=None,
    )

    assert changed is True
    assert item.image_url == "https://cdn.example.com/story-hero.jpg"


def test_extract_article_image_with_llm_validates_url_from_document(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    llm_client.extract_article_image_url.return_value = "/images/hero.jpg?fit=cover"
    hydrator = ArticleHydrationService(llm_client=llm_client)

    def fake_fetch(url):
        if url == "https://example.com/story":
            return FetchResult(
                url=url,
                status_code=200,
                html=(
                    "<html><head><title>Story</title></head><body><article>"
                    '<img src="/images/hero.jpg?fit=cover" alt="Lead image" />'
                    "</article></body></html>"
                ),
                content_type="text/html",
            )
        if url == "https://example.com/images/hero.jpg?fit=cover":
            return FetchResult(
                url=url,
                status_code=200,
                html="",
                content_type="image/jpeg",
            )
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr("app.extraction.fetcher.fetch_url", fake_fetch)

    image_url = hydrator.extract_article_image_with_llm(
        article_url="https://example.com/story",
        title="Story",
    )

    assert image_url == "https://example.com/images/hero.jpg?fit=cover"
    llm_client.extract_article_image_url.assert_not_called()


def test_extract_article_image_with_llm_uses_fresh_page_metadata_before_llm(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    hydrator = ArticleHydrationService(llm_client=llm_client)

    def fake_fetch(url):
        if url == "https://example.com/story":
            return FetchResult(
                url=url,
                status_code=200,
                html=(
                    "<html><head>"
                    '<meta property="og:image" content="https://example.com/images/hero.jpg" />'
                    "</head><body><article><p>Story</p></article></body></html>"
                ),
                content_type="text/html",
            )
        if url == "https://example.com/images/hero.jpg":
            return FetchResult(
                url=url,
                status_code=200,
                html="",
                content_type="image/jpeg",
            )
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(
        "app.extraction.fetcher.fetch_url",
        fake_fetch,
    )

    result = hydrator.extract_article_image_with_llm_diagnostics(
        article_url="https://example.com/story",
        title="Story",
    )

    assert result.image_url == "https://example.com/images/hero.jpg"
    assert result.reason == "fresh_page_metadata"
    llm_client.extract_article_image_url.assert_not_called()


def test_extract_article_image_with_llm_accepts_root_like_relative_asset_paths(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    llm_client.extract_article_image_url.return_value = (
        "news/2026/03/qcon-london-foxwell-dev-teams/en/resources/hero.jpg"
    )
    hydrator = ArticleHydrationService(llm_client=llm_client)

    def fake_fetch(url):
        if url == "https://www.infoq.com/news/2026/03/qcon-london-foxwell-dev-teams/":
            return FetchResult(
                url=url,
                status_code=200,
                html=(
                    "<html><body><article>"
                    '<img data-src="news/2026/03/qcon-london-foxwell-dev-teams/en/resources/hero.jpg" />'
                    "</article></body></html>"
                ),
                content_type="text/html",
            )
        if (
            url
            == "https://www.infoq.com/news/2026/03/qcon-london-foxwell-dev-teams/news/2026/03/qcon-london-foxwell-dev-teams/en/resources/hero.jpg"
        ):
            return FetchResult(
                url=url,
                status_code=404,
                html="",
                content_type="text/html",
                error="HTTP 404",
            )
        if (
            url
            == "https://www.infoq.com/news/2026/03/qcon-london-foxwell-dev-teams/en/resources/hero.jpg"
        ):
            return FetchResult(
                url=url,
                status_code=200,
                html="",
                content_type="image/jpeg",
            )
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr("app.extraction.fetcher.fetch_url", fake_fetch)

    image_url = hydrator.extract_article_image_with_llm(
        article_url="https://www.infoq.com/news/2026/03/qcon-london-foxwell-dev-teams/",
        title="Story",
    )

    assert (
        image_url
        == "https://www.infoq.com/news/2026/03/qcon-london-foxwell-dev-teams/en/resources/hero.jpg"
    )
    llm_client.extract_article_image_url.assert_not_called()


def test_extract_article_image_with_llm_rejects_invented_url(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    llm_client.extract_article_image_url.return_value = "https://cdn.example.com/invented.jpg"
    hydrator = ArticleHydrationService(llm_client=llm_client)

    monkeypatch.setattr(
        "app.extraction.fetcher.fetch_url",
        lambda url: FetchResult(
            url=url,
            status_code=200,
            html="<html><body><article><p>No image here.</p></article></body></html>",
            content_type="text/html",
        ),
    )

    image_url = hydrator.extract_article_image_with_llm(
        article_url="https://example.com/story",
        title="Story",
    )

    assert image_url is None


def test_extract_article_image_with_llm_diagnostics_reports_missing_llm():
    hydrator = ArticleHydrationService(llm_client=False)

    result = hydrator.extract_article_image_with_llm_diagnostics(
        article_url="https://example.com/story",
        title="Story",
    )

    assert result.image_url is None
    assert result.reason == "llm_not_configured"


def test_extract_article_image_with_llm_diagnostics_reports_bot_protected_fetch(monkeypatch):
    from app.extraction.fetcher import FetchResult

    hydrator = ArticleHydrationService(llm_client=MagicMock())

    monkeypatch.setattr(
        "app.extraction.fetcher.fetch_url",
        lambda url: FetchResult(
            url=url,
            status_code=403,
            html="",
            content_type="text/html",
            error="BOT_PROTECTED: cloudflare_challenge",
        ),
    )

    result = hydrator.extract_article_image_with_llm_diagnostics(
        article_url="https://example.com/story",
        title="Story",
    )

    assert result.image_url is None
    assert result.reason == "article_bot_protected"
    assert result.error == "BOT_PROTECTED: cloudflare_challenge"


def test_extract_article_image_with_llm_diagnostics_reports_validation_reason(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    llm_client.extract_article_image_url.return_value = "https://cdn.example.com/invented.jpg"
    hydrator = ArticleHydrationService(llm_client=llm_client)

    monkeypatch.setattr(
        "app.extraction.fetcher.fetch_url",
        lambda url: FetchResult(
            url=url,
            status_code=200,
            html="<html><body><article><p>No image here.</p></article></body></html>",
            content_type="text/html",
        ),
    )

    result = hydrator.extract_article_image_with_llm_diagnostics(
        article_url="https://example.com/story",
        title="Story",
    )

    assert result.image_url is None
    assert result.reason == "candidate_not_in_document"
    assert result.raw_candidate_url == "https://cdn.example.com/invented.jpg"


def test_extract_article_image_with_llm_uses_logo_fallback_after_strict_attempt(monkeypatch):
    from app.extraction.fetcher import FetchResult

    llm_client = MagicMock()
    llm_client.extract_article_image_url.side_effect = [
        "/images/company-logo.png",
        "/images/company-logo.png",
    ]
    hydrator = ArticleHydrationService(llm_client=llm_client)

    def fake_fetch(url):
        if url == "https://example.com/story":
            return FetchResult(
                url=url,
                status_code=200,
                html=(
                    "<html><body><article>"
                    '<img src="/images/company-logo.png" alt="Example company logo" />'
                    "</article></body></html>"
                ),
                content_type="text/html",
            )
        if url == "https://example.com/images/company-logo.png":
            return FetchResult(
                url=url,
                status_code=200,
                html="",
                content_type="image/png",
            )
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr("app.extraction.fetcher.fetch_url", fake_fetch)

    result = hydrator.extract_article_image_with_llm_diagnostics(
        article_url="https://example.com/story",
        title="Story",
    )

    assert result.image_url == "https://example.com/images/company-logo.png"
    assert result.reason == "generic_logo_fallback"
    assert llm_client.extract_article_image_url.call_count == 2
    assert llm_client.extract_article_image_url.call_args_list[0].kwargs["allow_logo_fallback"] is False
    assert llm_client.extract_article_image_url.call_args_list[1].kwargs["allow_logo_fallback"] is True


def test_refresh_existing_article_metadata_prefers_rss_image_over_weak_page_og(monkeypatch):
    hydrator = ArticleHydrationService()
    item = hydrator.build_article_stub(
        source_url="https://example.com/story",
        title="Recovered title",
        image_url="https://cdn.example.com/stale-rss-image.jpg",
    )
    item.canonical_url = "https://example.com/story"

    monkeypatch.setattr(
        hydrator,
        "fetch_article_page_metadata",
        lambda article_url: SimpleNamespace(
            title="Recovered title",
            canonical_url=article_url,
            image_url="https://s.yimg.com/kw/assets/engadget-amp-proposed.png",
            image_source="og",
        ),
    )

    changed = hydrator.refresh_existing_article_metadata(
        item,
        source_url="https://example.com/story",
        rss_image_url="https://cdn.example.com/rss-image.jpg",
        force_reconcile_image=True,
    )

    assert changed is True
    assert item.image_url == "https://cdn.example.com/rss-image.jpg"


def test_populate_article_summary_sets_summary_topics_and_starters():
    hydrator = ArticleHydrationService()
    item = hydrator.build_article_stub(
        source_url="https://example.com/story",
        title="Recovered title",
        content_text=("This is a detailed article body about infrastructure and deployment. " * 20),
    )
    item.ai_processed = False
    item.summary = None
    item.conversation_starters = None

    hydrator.summarize_article = lambda *_args, **_kwargs: SimpleNamespace(
        summary=(
            "This article explains how the team consolidated article ingestion, "
            "image extraction, and repair flows so every curation path shares "
            "the same enrichment logic."
        ),
        conversation_starters={"starters": ["What changed in the ingestion pipeline?"]},
        tags=["ingestion", "images"],
    )

    changed = hydrator.populate_article_summary(item)

    assert changed is True
    assert item.ai_processed is True
    assert item.summary.startswith("This article explains how the team consolidated")
    assert item.topics == ["ingestion", "images"]
    assert item.conversation_starters == {"starters": ["What changed in the ingestion pipeline?"]}
