from __future__ import annotations

import threading

from app import content_event_worker as worker


def test_resolve_event_types_parses_comma_separated_values():
    assert worker.resolve_event_types("content.ready, content.ai_summary.requested ,") == (
        "content.ready",
        "content.ai_summary.requested",
    )


def test_run_content_event_worker_uses_event_type_scope(monkeypatch):
    calls = []
    stop_event = threading.Event()

    class _FakeDispatcher:
        def __init__(self, *, event_types=None):
            calls.append(("init", tuple(event_types or ())))

        def process_pending(self, *, limit):
            calls.append(("process", limit))
            stop_event.set()
            return 1

    monkeypatch.setattr(worker, "ContentEventDispatcher", _FakeDispatcher)

    exit_code = worker.run_content_event_worker(
        event_types=("content.ai_summary.requested",),
        batch_size=25,
        poll_seconds=0.01,
        stop_event=stop_event,
    )

    assert exit_code == 0
    assert calls == [
        ("init", ("content.ai_summary.requested",)),
        ("process", 25),
    ]


def test_promotion_lane_does_not_pause_during_fetch(monkeypatch):
    stop_event = threading.Event()

    monkeypatch.setenv("CONTENT_EVENT_PAUSE_DURING_FETCH", "true")
    monkeypatch.setattr(worker, "is_job_active", lambda _job: True)
    monkeypatch.setattr(worker, "memory_over_soft_limit", lambda: False)

    paused = worker._pause_for_shared_worker_pressure(
        event_types=(worker.PROMOTION_EVENT_TYPE,),
        stop_event=stop_event,
        poll_seconds=0.01,
    )

    assert paused is False


def test_ai_lane_pauses_during_fetch(monkeypatch):
    stop_event = threading.Event()

    monkeypatch.setenv("CONTENT_EVENT_PAUSE_DURING_FETCH", "true")
    monkeypatch.setattr(worker, "is_job_active", lambda _job: True)

    paused = worker._pause_for_shared_worker_pressure(
        event_types=("content.ai_summary.requested",),
        stop_event=stop_event,
        poll_seconds=0.01,
    )

    assert paused is True
