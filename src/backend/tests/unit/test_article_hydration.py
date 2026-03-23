from types import SimpleNamespace

from app.article_hydration import ArticleHydrationService
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
