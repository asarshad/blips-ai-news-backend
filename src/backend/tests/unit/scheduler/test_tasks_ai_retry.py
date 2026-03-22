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

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()

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

    llm_client = MagicMock()
    llm_client.is_configured.return_value = True

    hydrator = MagicMock()

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
