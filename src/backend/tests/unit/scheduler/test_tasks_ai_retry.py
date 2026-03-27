from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentType
from app.scheduler import tasks_ai_retry


class _FakeStats:
    def __init__(self):
        self.llm_calls = 0
        self.items_processed = 0
        self.items_failed = 0
        self.items_skipped = 0
        self.errors = []

    def complete(self):
        return None

    def log_summary(self):
        return None


def test_process_ai_summaries_uses_article_hydrator_for_articles(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=42,
        type=ContentType.ARTICLE,
        content_text=("Detailed article text about infrastructure and release builds. " * 20),
        description=None,
        title="Hydrated article",
        topics=["old-topic"],
        conversation_starters=None,
        summary=None,
        ai_processed=False,
    )
    repo.get_unprocessed_by_ai.return_value = [item]
    repo.get_articles_with_short_summaries.return_value = []
    repo.get_articles_with_long_summaries.return_value = []

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()
    hydrator.run_article_extraction.return_value = None  # don't corrupt item during retry-refresh

    def _populate(target):
        target.summary = (
            "Hydrated article summary with enough detail to exceed the minimum length "
            "threshold for persisted summaries in the retry worker."
        )
        target.topics = ["ingestion", "release"]
        target.conversation_starters = {"starters": ["Which fix changed the retry path?"]}
        target.ai_processed = True
        return True

    hydrator.populate_article_summary.side_effect = _populate

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: _FakeStats())
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService",
        lambda llm_client=None: hydrator,
    )
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    hydrator.populate_article_summary.assert_called_once_with(item)
    hydrator.refresh_article_annotations.assert_called_once_with(item)
    repo.mark_ai_processed.assert_called_once_with(
        42,
        summary=item.summary,
        topics=["ingestion", "release"],
    )


def test_process_ai_summaries_retries_short_article_summaries(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=7,
        type=ContentType.ARTICLE,
        content_text=(
            "Detailed article text about release engineering and infra resilience. " * 20
        ),
        description=None,
        title="Short summary article",
        topics=["old-topic"],
        conversation_starters=None,
        summary="Too short to satisfy the new target.",
        ai_processed=True,
    )
    repo.get_unprocessed_by_ai.return_value = []
    repo.get_articles_with_short_summaries.return_value = [item]
    repo.get_articles_with_long_summaries.return_value = []

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()
    hydrator.run_article_extraction.return_value = None  # don't corrupt item during retry-refresh

    def _populate(target):
        target.summary = (
            "This regenerated article summary is intentionally long enough to clear the new "
            "minimum word target while still staying concise and useful for the feed experience."
        )
        target.topics = ["release", "platform"]
        target.conversation_starters = {"starters": ["Which rollout detail matters most here?"]}
        target.ai_processed = True
        return True

    hydrator.populate_article_summary.side_effect = _populate

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: _FakeStats())
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService",
        lambda llm_client=None: hydrator,
    )
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    assert item.ai_processed is True
    assert item.summary.startswith("This regenerated article summary")
    hydrator.populate_article_summary.assert_called_once_with(item)
    repo.mark_ai_processed.assert_called_once_with(
        7,
        summary=item.summary,
        topics=["release", "platform"],
    )


def test_process_ai_summaries_retries_long_article_summaries(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=99,
        type=ContentType.ARTICLE,
        content_text=("Detailed article text about AI infrastructure and hiring. " * 20),
        description=None,
        title="Long summary article",
        topics=["old-topic"],
        conversation_starters=None,
        summary=" ".join(f"word{i}" for i in range(71)),
        ai_processed=True,
    )
    repo.get_unprocessed_by_ai.return_value = []
    repo.get_articles_with_short_summaries.return_value = []
    repo.get_articles_with_long_summaries.return_value = [item]

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()
    hydrator.run_article_extraction.return_value = None  # don't corrupt item during retry-refresh

    def _populate(target):
        target.summary = (
            "OpenAI plans to expand its workforce this year, signaling heavier investment "
            "in research, product delivery, and go-to-market execution as demand for "
            "enterprise AI tools keeps rising."
        )
        target.topics = ["openai", "hiring"]
        target.conversation_starters = {
            "starters": ["Why is OpenAI expanding headcount this quickly?"]
        }
        target.ai_processed = True
        return True

    hydrator.populate_article_summary.side_effect = _populate

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: _FakeStats())
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService",
        lambda llm_client=None: hydrator,
    )
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    assert item.ai_processed is True
    assert len(item.summary.split()) <= 70
    hydrator.populate_article_summary.assert_called_once_with(item)
    repo.mark_ai_processed.assert_called_once_with(
        99,
        summary=item.summary,
        topics=["openai", "hiring"],
    )


def test_process_ai_summaries_skips_empty_article_input_without_error(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=15,
        type=ContentType.ARTICLE,
        content_text=None,
        description=None,
        title="Health NZ staff told to stop using ChatGPT to write",
        canonical_url=None,
        published_at=None,
        image_url=None,
        topics=[],
        conversation_starters=None,
        summary=None,
        ai_processed=False,
    )
    repo.get_unprocessed_by_ai.return_value = [item]
    repo.get_articles_with_short_summaries.return_value = []
    repo.get_articles_with_long_summaries.return_value = []

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()
    hydrator.run_article_extraction.return_value = None
    hydrator.should_replace_article_image.return_value = False

    stats = _FakeStats()

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: stats)
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService",
        lambda llm_client=None: hydrator,
    )
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    assert stats.items_skipped == 1
    assert stats.items_failed == 0
    assert stats.errors == []
    # Article is permanently marked processed with empty summary so it stops
    # re-entering the retry queue, but remains PENDING/missing_article_summary
    # in the feed (evaluate_content_readiness checks for non-empty summary).
    repo.mark_ai_processed.assert_called_once_with(15, summary="", topics=[])
    llm_client.summarize_article.assert_not_called()


def test_process_ai_summaries_persists_extracted_article_fields_before_summary(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=27,
        type=ContentType.ARTICLE,
        content_text=None,
        description=None,
        title="Pending article",
        canonical_url=None,
        published_at=None,
        image_url=None,
        topics=[],
        conversation_starters=None,
        summary=None,
        ai_processed=False,
    )
    repo.get_unprocessed_by_ai.return_value = [item]
    repo.get_articles_with_short_summaries.return_value = []
    repo.get_articles_with_long_summaries.return_value = []

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()
    hydrator.run_article_extraction.return_value = SimpleNamespace(
        title="Recovered title",
        canonical_url="https://example.com/story",
        published_at=None,
        main_text=("Recovered article body with enough words for summarization. " * 20),
        excerpt_fallback=None,
        image_url="https://cdn.example.com/story-hero.jpg",
    )
    hydrator.should_replace_article_image.return_value = True

    def _populate(target):
        target.summary = (
            "Recovered article summary with enough detail to exceed the minimum "
            "length threshold and keep the retry worker happy."
        )
        target.topics = ["policy", "health"]
        target.ai_processed = True
        return True

    hydrator.populate_article_summary.side_effect = _populate

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: _FakeStats())
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(
        "app.article_hydration.ArticleHydrationService",
        lambda llm_client=None: hydrator,
    )
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    assert item.title == "Recovered title"
    assert item.canonical_url == "https://example.com/story"
    assert item.image_url == "https://cdn.example.com/story-hero.jpg"
    assert item.content_text.startswith("Recovered article body")
    repo.mark_ai_processed.assert_called_once_with(
        27,
        summary=item.summary,
        topics=["policy", "health"],
    )


def test_process_ai_summaries_rejects_short_video_summary(monkeypatch):
    db = MagicMock()
    repo = MagicMock()
    item = SimpleNamespace(
        id=55,
        type=ContentType.VIDEO,
        content_text="This description has enough detail to trigger video summarization.",
        description=None,
        title="Video with weak summary",
        topics=["video"],
        conversation_starters=None,
        summary=None,
        ai_processed=False,
    )
    repo.get_unprocessed_by_ai.return_value = [item]
    repo.get_articles_with_short_summaries.return_value = []
    repo.get_articles_with_long_summaries.return_value = []

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True
    llm_client.summarize_video.return_value = SimpleNamespace(
        summary="Too short to keep.",
        conversation_starters={"starters": ["What stood out in this video?"]},
    )

    monkeypatch.setattr(tasks_ai_retry.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ai_retry, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_ai_retry, "log_job_start", lambda _name: _FakeStats())
    monkeypatch.setattr(tasks_ai_retry, "_backfill_starters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.integrations.LLMClient", lambda: llm_client)
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: repo)
    monkeypatch.setattr(tasks_ai_retry.time, "sleep", lambda *_args, **_kwargs: None)

    tasks_ai_retry.process_ai_summaries()

    repo.mark_ai_processed.assert_not_called()
