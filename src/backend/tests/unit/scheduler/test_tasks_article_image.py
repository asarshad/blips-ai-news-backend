"""Unit tests for the dedicated article image verification scheduler job."""

from unittest.mock import MagicMock

from app.scheduler import tasks_article_image
from app.scheduler.runtime import (
    reset_job_runtime_state,
)


class _FakeStats:
    def __init__(self):
        self.items_processed = 0
        self.items_skipped = 0
        self.items_failed = 0
        self.errors: list[str] = []

    def complete(self):
        return None

    def log_summary(self):
        return None


def setup_function():
    reset_job_runtime_state()


def test_run_article_image_verification_job_does_not_wait_on_fetch_state(monkeypatch):
    """The async image lane owns its own resource limits and does not wait on ingestion."""
    db = MagicMock()
    repair_calls = []

    monkeypatch.setattr(tasks_article_image, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks_article_image,
        "repair_article_image_metadata",
        lambda *args, **kwargs: (
            repair_calls.append((args, kwargs))
            or {"scanned": 1, "updated": 0, "failures": 0}
        ),
    )
    monkeypatch.setattr(tasks_article_image, "log_job_start", lambda _name: _FakeStats())

    tasks_article_image.run_article_image_verification_job()

    assert len(repair_calls) == 1


def test_run_article_image_verification_job_runs_when_fetch_news_is_idle(monkeypatch):
    db = MagicMock()
    repair_calls = []

    monkeypatch.setattr(tasks_article_image, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks_article_image,
        "repair_article_image_metadata",
        lambda *args, **kwargs: (
            repair_calls.append((args, kwargs))
            or {"scanned": 3, "updated": 2, "failures": 0}
        ),
    )
    monkeypatch.setattr(tasks_article_image, "log_job_start", lambda _name: _FakeStats())

    tasks_article_image.run_article_image_verification_job()

    assert len(repair_calls) == 1
    _, kwargs = repair_calls[0]
    # Job targets promoted rows that are actually blocking readiness.
    assert kwargs["promoted_only"] is True
    assert kwargs["readiness_reasons"] == (
        "missing_article_image",
        "awaiting_article_image_verification",
    )
    assert kwargs["max_seconds"] == tasks_article_image.settings.ARTICLE_IMAGE_REPAIR_MAX_SECONDS
    # DB session is always released.
    assert db.close.called


def test_run_article_image_verification_job_closes_session_on_failure(monkeypatch):
    db = MagicMock()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(tasks_article_image, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks_article_image, "repair_article_image_metadata", _boom)
    monkeypatch.setattr(tasks_article_image, "log_job_start", lambda _name: _FakeStats())

    # Must not raise — job swallows exceptions and logs them instead.
    tasks_article_image.run_article_image_verification_job()
    assert db.close.called
