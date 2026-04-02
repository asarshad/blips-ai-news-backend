from unittest.mock import MagicMock

from app.scheduler import tasks_curation, tasks_promotion
from app.scheduler.runtime import (
    CLUSTERING_JOB,
    FETCH_NEWS_INLINE_CLUSTERING,
    FETCH_NEWS_INLINE_PROMOTION,
    FETCH_NEWS_JOB,
    PROMOTION_JOB,
    is_job_active,
    mark_job_finished,
    mark_job_started,
    reset_job_runtime_state,
)


def setup_function():
    reset_job_runtime_state()


def test_scheduled_clustering_skips_when_fetch_news_is_active(monkeypatch):
    started_at = mark_job_started(FETCH_NEWS_JOB)
    try:
        monkeypatch.setattr(tasks_curation.feature_flags, "is_enabled", lambda name: True)
        session_factory = MagicMock()
        monkeypatch.setattr(tasks_curation, "SessionLocal", session_factory)

        tasks_curation.run_clustering_job()

        session_factory.assert_not_called()
    finally:
        mark_job_finished(FETCH_NEWS_JOB, started_at, success=False)


def test_scheduled_clustering_skips_after_recent_inline_run(monkeypatch):
    inline_started_at = mark_job_started(FETCH_NEWS_INLINE_CLUSTERING)
    mark_job_finished(FETCH_NEWS_INLINE_CLUSTERING, inline_started_at, success=True)

    monkeypatch.setattr(tasks_curation.feature_flags, "is_enabled", lambda name: True)
    session_factory = MagicMock()
    monkeypatch.setattr(tasks_curation, "SessionLocal", session_factory)

    tasks_curation.run_clustering_job()

    session_factory.assert_not_called()


def test_scheduled_promotion_skips_when_fetch_news_is_active(monkeypatch):
    started_at = mark_job_started(FETCH_NEWS_JOB)
    try:
        monkeypatch.setattr(tasks_promotion.feature_flags, "is_enabled", lambda name: True)
        session_factory = MagicMock()
        monkeypatch.setattr(tasks_promotion, "SessionLocal", session_factory)

        tasks_promotion.run_promotion_job()

        session_factory.assert_not_called()
    finally:
        mark_job_finished(FETCH_NEWS_JOB, started_at, success=False)


def test_scheduled_promotion_skips_after_recent_inline_run(monkeypatch):
    inline_started_at = mark_job_started(FETCH_NEWS_INLINE_PROMOTION)
    mark_job_finished(FETCH_NEWS_INLINE_PROMOTION, inline_started_at, success=True)

    monkeypatch.setattr(tasks_promotion.feature_flags, "is_enabled", lambda name: True)
    session_factory = MagicMock()
    monkeypatch.setattr(tasks_promotion, "SessionLocal", session_factory)

    tasks_promotion.run_promotion_job()

    session_factory.assert_not_called()


def test_scheduled_clustering_clears_runtime_state_when_session_setup_fails(monkeypatch):
    monkeypatch.setattr(tasks_curation.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_curation, "SessionLocal", MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(tasks_curation, "log_job_start", lambda _name: MagicMock())

    tasks_curation.run_clustering_job(trigger="fetch_news")

    assert is_job_active(CLUSTERING_JOB) is False


def test_scheduled_promotion_clears_runtime_state_when_session_setup_fails(monkeypatch):
    monkeypatch.setattr(tasks_promotion.feature_flags, "is_enabled", lambda name: True)
    monkeypatch.setattr(tasks_promotion, "SessionLocal", MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(tasks_promotion, "log_job_start", lambda _name: MagicMock())

    tasks_promotion.run_promotion_job(trigger="fetch_news")

    assert is_job_active(PROMOTION_JOB) is False
