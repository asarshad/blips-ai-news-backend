"""Unit tests for the dedicated background worker entrypoint."""

from __future__ import annotations

import app.worker as worker


def _patch_signal_handlers(monkeypatch):
    monkeypatch.setattr(worker.signal, "signal", lambda *_args, **_kwargs: None)


def test_run_worker_returns_zero_when_scheduler_disabled(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    idle_calls: list[str] = []
    monkeypatch.setattr(worker, "_idle_forever", lambda: idle_calls.append("idle"))

    exit_code = worker.run_worker()

    assert exit_code == 0
    assert idle_calls == ["idle"]


def test_run_worker_exits_nonzero_when_lock_never_acquired(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(worker, "_acquire_lock_with_retry", lambda: False)
    monkeypatch.setattr(worker, "_idle_forever", lambda: (_ for _ in ()).throw(AssertionError()))

    exit_code = worker.run_worker()

    assert exit_code == 1


def test_acquire_lock_with_retry_retries_redis_unavailability(monkeypatch):
    worker._stop_event.clear()
    attempts = iter([None, True])
    waits: list[float] = []

    monkeypatch.setattr(worker, "acquire_worker_lock", lambda: next(attempts))
    monkeypatch.setattr(
        worker._stop_event,
        "wait",
        lambda timeout=None: waits.append(timeout) or False,
    )

    assert worker._acquire_lock_with_retry(max_attempts=3, base_delay=5.0) is True
    assert waits == [5.0]


def test_run_worker_refreshes_lock_during_startup_fetch(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(worker, "_acquire_lock_with_retry", lambda: True)

    class DummyScheduler:
        def shutdown(self, wait=False):  # noqa: ARG002
            return None

    monkeypatch.setattr(worker, "init_scheduler", lambda: DummyScheduler())
    monkeypatch.setattr(worker, "fetch_and_process_news", lambda: None)

    started_threads: list[tuple[object, bool | None, str | None]] = []
    joined_threads: list[tuple[str | None, float | None]] = []

    class DummyThread:
        def __init__(self, target=None, daemon=None, name=None):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started_threads.append((self.target, self.daemon, self.name))

        def join(self, timeout=None):
            joined_threads.append((self.name, timeout))

    monkeypatch.setattr(worker.threading, "Thread", DummyThread)
    monkeypatch.setattr(
        worker,
        "_maintain_worker_lock",
        lambda *args, **kwargs: worker._stop_event.set() or 0,
    )
    monkeypatch.setattr(worker, "release_worker_lock", lambda: True)

    exit_code = worker.run_worker()

    assert exit_code == 0
    assert any(name == "worker-startup-lock-refresher" and daemon for _, daemon, name in started_threads)
    assert ("worker-startup-lock-refresher", None) in joined_threads


def test_resolve_content_event_worker_specs_uses_default_groups(monkeypatch):
    monkeypatch.delenv("CONTENT_EVENT_WORKER_SPECS", raising=False)

    assert worker._resolve_content_event_worker_specs() == (
        ("content.promotion_eval.requested",),
        ("content.ai_summary.requested",),
        (
            "article.image_verification.requested",
            "content.ready",
            "content.unready",
        ),
    )


def test_resolve_content_event_worker_specs_parses_semicolon_groups(monkeypatch):
    monkeypatch.setenv(
        "CONTENT_EVENT_WORKER_SPECS",
        "content.ready,content.unready; content.ai_summary.requested ",
    )

    assert worker._resolve_content_event_worker_specs() == (
        ("content.ready", "content.unready"),
        ("content.ai_summary.requested",),
    )


def test_startup_content_event_worker_specs_only_includes_promotion(monkeypatch):
    monkeypatch.setenv(
        "CONTENT_EVENT_WORKER_SPECS",
        "content.promotion_eval.requested;content.ai_summary.requested",
    )

    assert worker._startup_content_event_worker_specs() == (
        ("content.promotion_eval.requested",),
    )
    assert worker._steady_state_content_event_worker_specs() == (
        ("content.ai_summary.requested",),
    )


def test_run_worker_starts_content_event_threads_when_enabled(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_EVENT_THREADS_ENABLED", "true")
    monkeypatch.setattr(worker, "_acquire_lock_with_retry", lambda: True)

    class DummyScheduler:
        def shutdown(self, wait=False):  # noqa: ARG002
            return None

    monkeypatch.setattr(worker, "init_scheduler", lambda: DummyScheduler())
    monkeypatch.setattr(worker, "fetch_and_process_news", lambda: None)

    started_specs: list[tuple[object, tuple[tuple[str, ...], ...] | None]] = []

    def _fake_start_threads(stop_event, *, specs=None):
        started_specs.append((stop_event, specs))
        return []

    monkeypatch.setattr(worker, "_start_content_event_worker_threads", _fake_start_threads)
    monkeypatch.setattr(
        worker,
        "_maintain_worker_lock",
        lambda *args, **kwargs: worker._stop_event.set() or 0,
    )
    monkeypatch.setattr(worker, "release_worker_lock", lambda: True)

    exit_code = worker.run_worker()

    assert exit_code == 0
    assert started_specs == [
        (worker._stop_event, (("content.promotion_eval.requested",),)),
        (
            worker._stop_event,
            (
                ("content.ai_summary.requested",),
                (
                    "article.image_verification.requested",
                    "content.ready",
                    "content.unready",
                ),
            ),
        ),
    ]


def test_maintain_worker_lock_exits_nonzero_when_reacquire_fails(monkeypatch):
    worker._stop_event.clear()
    stop_event = worker.threading.Event()

    monkeypatch.setattr(stop_event, "wait", lambda timeout=None: False)
    monkeypatch.setattr(worker, "refresh_worker_lock", lambda: False)
    monkeypatch.setattr(worker, "_attempt_lock_reacquire", lambda: False)

    assert (
        worker._maintain_worker_lock(
            stop_event,
            refresh_interval_seconds=0,
            phase="test",
        )
        == 1
    )


def test_run_worker_releases_lock_and_exits_nonzero_when_scheduler_init_fails(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(worker, "_acquire_lock_with_retry", lambda: True)
    monkeypatch.setattr(worker, "init_scheduler", lambda: None)
    monkeypatch.setattr(worker._stop_event, "wait", lambda timeout=None: False)

    release_calls: list[str] = []
    monkeypatch.setattr(worker, "release_worker_lock", lambda: release_calls.append("release") or True)

    exit_code = worker.run_worker()

    assert exit_code == 1
    assert release_calls == ["release"]


def test_run_worker_exits_nonzero_when_lock_recovery_fails(monkeypatch):
    _patch_signal_handlers(monkeypatch)
    worker._stop_event.clear()
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(worker, "_acquire_lock_with_retry", lambda: True)

    class DummyScheduler:
        def shutdown(self, wait=False):  # noqa: ARG002
            return None

    monkeypatch.setattr(worker, "init_scheduler", lambda: DummyScheduler())
    monkeypatch.setattr(worker, "fetch_and_process_news", lambda: None)

    class DummyThread:
        def __init__(self, target=None, daemon=None, name=None):  # noqa: ARG002
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            return None

        def join(self, timeout=None):  # noqa: ARG002
            return None

    monkeypatch.setattr(worker.threading, "Thread", DummyThread)
    monkeypatch.setattr(worker, "_maintain_worker_lock", lambda *args, **kwargs: 1)

    release_calls: list[str] = []
    monkeypatch.setattr(worker, "release_worker_lock", lambda: release_calls.append("release") or True)

    exit_code = worker.run_worker()

    assert exit_code == 1
    assert release_calls == ["release"]
