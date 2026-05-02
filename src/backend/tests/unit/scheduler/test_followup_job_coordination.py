from unittest.mock import MagicMock

from app.scheduler import tasks_curation, tasks_promotion
from app.scheduler.runtime import (
    CLUSTERING_JOB,
    FETCH_NEWS_JOB,
    PROMOTION_JOB,
    is_job_active,
    mark_job_finished,
    mark_job_started,
    reset_job_runtime_state,
    try_mark_job_started,
)


def setup_function():
    reset_job_runtime_state()


def test_scheduled_clustering_does_not_wait_on_fetch_state(monkeypatch):
    started_at = mark_job_started(FETCH_NEWS_JOB)
    try:
        monkeypatch.setattr(tasks_curation.feature_flags, "is_enabled", lambda name: True)
        db = MagicMock()
        monkeypatch.setattr(tasks_curation, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_curation, "log_job_start", lambda _name: MagicMock())
        monkeypatch.setattr(
            "app.clustering.ClusteringService",
            lambda _repo: MagicMock(run_clustering_job=lambda: {"items_clustered": 0}),
        )
        monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: MagicMock())

        tasks_curation.run_clustering_job()

        assert db.close.called
    finally:
        mark_job_finished(FETCH_NEWS_JOB, started_at, success=False)


def test_scheduled_clustering_runs_after_recent_inline_state(monkeypatch):
    monkeypatch.setattr(tasks_curation.feature_flags, "is_enabled", lambda name: True)
    db = MagicMock()
    monkeypatch.setattr(tasks_curation, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_curation, "log_job_start", lambda _name: MagicMock())
    monkeypatch.setattr(
        "app.clustering.ClusteringService",
        lambda _repo: MagicMock(run_clustering_job=lambda: {"items_clustered": 0}),
    )
    monkeypatch.setattr("app.repositories.content_repo.ContentItemRepository", lambda _db: MagicMock())

    tasks_curation.run_clustering_job()

    assert db.close.called


def test_scheduled_promotion_does_not_wait_on_fetch_state(monkeypatch):
    started_at = mark_job_started(FETCH_NEWS_JOB)
    try:
        monkeypatch.setattr(tasks_promotion.feature_flags, "is_enabled", lambda name: True)
        db = MagicMock()
        monkeypatch.setattr(tasks_promotion, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_promotion, "log_job_start", lambda _name: MagicMock())
        monkeypatch.setattr(
            "app.services.promotion_service.PromotionService",
            lambda _db: MagicMock(
                run_promotion_job=lambda: MagicMock(
                    promoted_count=0,
                    candidates_evaluated=0,
                    already_promoted_rescored=0,
                    errors=[],
                )
            ),
        )

        tasks_promotion.run_promotion_job()

        assert db.close.called
    finally:
        mark_job_finished(FETCH_NEWS_JOB, started_at, success=False)


def test_scheduled_promotion_runs_after_recent_inline_state(monkeypatch):
    monkeypatch.setattr(tasks_promotion.feature_flags, "is_enabled", lambda name: True)
    db = MagicMock()
    monkeypatch.setattr(tasks_promotion, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_promotion, "log_job_start", lambda _name: MagicMock())
    monkeypatch.setattr(
        "app.services.promotion_service.PromotionService",
        lambda _db: MagicMock(
            run_promotion_job=lambda: MagicMock(
                promoted_count=0,
                candidates_evaluated=0,
                already_promoted_rescored=0,
                errors=[],
            )
        ),
    )

    tasks_promotion.run_promotion_job()

    assert db.close.called


def test_try_mark_job_started_is_atomic_with_blockers():
    fetch_started_at = mark_job_started(FETCH_NEWS_JOB)
    try:
        assert (
            try_mark_job_started(
                "article_image_verification",
                unless_active=(FETCH_NEWS_JOB,),
            )
            is None
        )
        assert is_job_active("article_image_verification") is False
    finally:
        mark_job_finished(FETCH_NEWS_JOB, fetch_started_at, success=False)


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
