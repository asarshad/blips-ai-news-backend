from __future__ import annotations

from types import SimpleNamespace

from app.scheduler.tasks_content_events import (
    run_article_image_event_dispatch_job,
    run_content_event_dispatch_job,
)
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
from app.services.content_readiness import CONTENT_READY_EVENT_TYPE, CONTENT_UNREADY_EVENT_TYPE


def _fake_stats():
    return SimpleNamespace(
        items_processed=0,
        errors=[],
        complete=lambda: None,
        log_summary=lambda: None,
    )


def test_run_content_event_dispatch_job_claims_only_ready_and_unready(monkeypatch):
    calls: list[tuple[tuple[str, ...] | None, int]] = []

    class _FakeDispatcher:
        def __init__(self, *, event_types=None, **_kwargs):
            self._event_types = event_types

        def process_pending(self, *, limit):
            calls.append((self._event_types, limit))
            return 3

    monkeypatch.setattr("app.scheduler.tasks_content_events.log_job_start", lambda _name: _fake_stats())
    monkeypatch.setattr("app.scheduler.tasks_content_events.ContentEventDispatcher", _FakeDispatcher)

    run_content_event_dispatch_job()

    assert calls == [((CONTENT_READY_EVENT_TYPE, CONTENT_UNREADY_EVENT_TYPE), 100)]


def test_run_article_image_event_dispatch_job_claims_only_image_events(monkeypatch):
    calls: list[tuple[tuple[str, ...] | None, int]] = []

    class _FakeDispatcher:
        def __init__(self, *, event_types=None, **_kwargs):
            self._event_types = event_types

        def process_pending(self, *, limit):
            calls.append((self._event_types, limit))
            return 2

    monkeypatch.setattr("app.scheduler.tasks_content_events.log_job_start", lambda _name: _fake_stats())
    monkeypatch.setattr("app.scheduler.tasks_content_events.ContentEventDispatcher", _FakeDispatcher)

    run_article_image_event_dispatch_job()

    assert calls == [((ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,), 100)]
