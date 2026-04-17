import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.scheduler import tasks_ingestion
from app.scheduler.job_stats import JobStats


def test_run_curation_ingestion_runs_video_discovery(monkeypatch):
    checkpoint_calls = []
    discovery_calls = []
    db = object()

    monkeypatch.setitem(
        sys.modules,
        "app.ingestion.checkpointing",
        SimpleNamespace(
            run_checkpointed_ingestion=lambda db, redis_client=None: (
                checkpoint_calls.append((db, redis_client)) or {"status": "ok"}
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.ingestion.service",
        SimpleNamespace(
            run_video_discovery_ingestion=lambda db: (
                discovery_calls.append(db)
                or {"videos_ingested": 2, "reels_ingested": 1, "errors": 0}
            )
        ),
    )
    monkeypatch.setattr(tasks_ingestion, "get_redis", lambda: None)
    monkeypatch.setattr(tasks_ingestion, "youtube_discovery_enabled", lambda: True)

    stats = JobStats(job_name="fetch_news")
    tasks_ingestion._run_curation_ingestion_with_stats(db, stats)

    assert checkpoint_calls == [(db, None)]
    assert discovery_calls == [db]
    assert stats.items_processed == 3
    assert stats.errors == []


def test_fetch_and_process_news_respects_immediate_ai_override(monkeypatch):
    db = MagicMock()
    ai_calls = []

    monkeypatch.setattr(tasks_ingestion.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ingestion, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks_ingestion,
        "log_job_start",
        lambda _name: JobStats(job_name="fetch_news"),
    )
    monkeypatch.setattr(
        tasks_ingestion,
        "_run_curation_ingestion_with_stats",
        lambda _db, _stats: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "app.scheduler.tasks_ai_retry",
        SimpleNamespace(
            process_ai_summaries=lambda **kwargs: ai_calls.append(kwargs),
        ),
    )
    monkeypatch.setenv("IMMEDIATE_AI_SUMMARY_MAX_ITEMS", "7")

    tasks_ingestion.fetch_and_process_news()

    assert ai_calls == [{"max_items": 7, "include_maintenance": False, "trigger": "fetch_news"}]


def test_fetch_and_process_news_defaults_immediate_ai_to_article_priority_limit(monkeypatch):
    db = MagicMock()
    ai_calls = []

    monkeypatch.delenv("IMMEDIATE_AI_SUMMARY_MAX_ITEMS", raising=False)
    monkeypatch.setattr(tasks_ingestion.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_ingestion, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks_ingestion,
        "log_job_start",
        lambda _name: JobStats(job_name="fetch_news"),
    )
    monkeypatch.setattr(
        tasks_ingestion,
        "_run_curation_ingestion_with_stats",
        lambda _db, _stats: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "app.scheduler.tasks_ai_retry",
        SimpleNamespace(
            process_ai_summaries=lambda **kwargs: ai_calls.append(kwargs),
        ),
    )
    monkeypatch.setattr(tasks_ingestion.settings, "ARTICLE_AI_PRIORITY_MAX_ITEMS_PER_RUN", 250)

    tasks_ingestion.fetch_and_process_news()

    assert ai_calls == [{"max_items": 250, "include_maintenance": False, "trigger": "fetch_news"}]
