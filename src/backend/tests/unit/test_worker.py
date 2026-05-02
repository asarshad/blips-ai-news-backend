"""Unit tests for the worker launcher entrypoints."""

from __future__ import annotations

import app.worker as worker
from app.workers import launcher
from app.workers.leader_lock import WorkerLeaderLock


def test_app_worker_delegates_to_launcher(monkeypatch):
    monkeypatch.setattr(worker, "run_launcher", lambda: 7)

    assert worker.run_worker() == 7


def test_launcher_defines_independent_lane_processes():
    lane_names = [lane.name for lane in launcher.DEFAULT_LANES]

    assert lane_names == [
        "ingestion",
        "promotion",
        "ai_summary",
        "article_images",
        "ready_events",
        "maintenance",
    ]


def test_launcher_uses_per_lane_db_pool_env(monkeypatch):
    lane = launcher.DEFAULT_LANES[0]
    monkeypatch.setenv("LANE_DB_POOL_SIZE", "1")
    monkeypatch.setenv("LANE_DB_MAX_OVERFLOW", "1")
    monkeypatch.setenv("DB_POOL_SIZE", "9")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "9")

    env = launcher._child_env(lane)

    assert env["WORKER_LANE_NAME"] == "ingestion"
    assert env["DB_POOL_SIZE"] == "1"
    assert env["DB_MAX_OVERFLOW"] == "1"


def test_leader_lock_acquire_retries_redis_unavailability(monkeypatch):
    lock = WorkerLeaderLock(key="test-lock", ttl_seconds=30, token="token")
    stop_event = launcher.threading.Event()
    attempts = iter([None, True])
    waits: list[float] = []

    monkeypatch.setattr(lock, "acquire", lambda: next(attempts))
    monkeypatch.setattr(
        stop_event,
        "wait",
        lambda timeout=None: waits.append(timeout) or False,
    )

    assert lock.acquire_with_retry(stop_event, max_attempts=3, base_delay_seconds=5.0) is True
    assert waits == [5.0]


def test_launcher_detects_stale_lane_heartbeat(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "read_lane_heartbeats",
        lambda: {
            "available": True,
            "lanes": [
                {
                    "lane": "ingestion",
                    "status": "idle",
                    "updated_at": "2026-05-02T10:00:00+00:00",
                }
            ],
        },
    )

    class _FakeDateTime:
        @staticmethod
        def fromisoformat(value):
            from datetime import datetime

            return datetime.fromisoformat(value)

        @staticmethod
        def now(tz=None):
            from datetime import datetime

            return datetime(2026, 5, 2, 10, 20, 1, tzinfo=tz)

    monkeypatch.setattr(launcher, "datetime", _FakeDateTime)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: 1000.0)

    stale = launcher._stale_lanes(
        {"ingestion", "ai_summary"},
        stale_after_seconds=900,
        startup_grace_seconds=120,
        launched_at=0.0,
    )

    assert stale == ["ai_summary", "ingestion"]


def test_launcher_ignores_stale_heartbeats_during_startup_grace(monkeypatch):
    monkeypatch.setattr(launcher, "read_lane_heartbeats", lambda: {"available": True, "lanes": []})
    monkeypatch.setattr(launcher.time, "monotonic", lambda: 10.0)

    stale = launcher._stale_lanes(
        {"ingestion"},
        stale_after_seconds=1,
        startup_grace_seconds=120,
        launched_at=0.0,
    )

    assert stale == []


def test_launcher_does_not_fail_when_heartbeat_snapshot_unavailable(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "read_lane_heartbeats",
        lambda: {"available": False, "error": "redis down", "lanes": []},
    )
    monkeypatch.setattr(launcher.time, "monotonic", lambda: 1000.0)

    stale = launcher._stale_lanes(
        {"ingestion"},
        stale_after_seconds=1,
        startup_grace_seconds=120,
        launched_at=0.0,
    )

    assert stale == []


def test_stop_lanes_uses_per_lane_grace_window():
    waits: list[float] = []

    class _FakeProcess:
        def __init__(self):
            self.returncode = None
            self.pid = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def wait(self, timeout=None):
            waits.append(timeout)
            self.returncode = 0
            return 0

    launcher._stop_lanes({"one": _FakeProcess(), "two": _FakeProcess()}, grace_seconds=7)

    assert waits == [7, 7]


def test_start_lanes_stops_started_lanes_when_spawn_fails(monkeypatch):
    stopped: list[str] = []
    calls = []

    class _FakeProcess:
        pid = 123

    def _fake_popen(command, env=None):  # noqa: ARG001
        calls.append(command)
        if len(calls) == 2:
            raise OSError("spawn failed")
        return _FakeProcess()

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(launcher, "_stop_lanes", lambda processes: stopped.extend(processes))
    monkeypatch.setattr(launcher, "record_lane_heartbeat", lambda *a, **k: None)

    try:
        launcher._start_lanes(
            (
                launcher.LaneSpec("one", "app.one"),
                launcher.LaneSpec("two", "app.two"),
            )
        )
    except OSError:
        pass
    else:
        raise AssertionError("Expected spawn failure")

    assert stopped == ["one"]


def test_leader_lock_refresh_and_release_use_cas(monkeypatch):
    calls = []

    class _FakeRedis:
        def eval(self, *args):
            calls.append(args)
            return 1

    monkeypatch.setattr("app.workers.leader_lock.get_redis", lambda: _FakeRedis())
    lock = WorkerLeaderLock(key="k", ttl_seconds=30, token="tok")

    assert lock.refresh() is True
    assert lock.release() is True
    assert calls[0][2:] == ("k", "tok", "30")
    assert calls[1][2:] == ("k", "tok")


def test_run_launcher_exits_when_lock_refresh_repeatedly_fails(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    launcher.STOP_EVENT.clear()

    class _FakeLock:
        refresh_interval_seconds = 0

        def acquire_with_retry(self, stop_event):  # noqa: ARG002
            return True

        def refresh(self):
            return None

        def release(self):
            return True

    class _FakeProcess:
        pid = 1
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):  # noqa: ARG002
            self.returncode = 0
            return 0

    monkeypatch.setattr(launcher.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "WorkerLeaderLock", _FakeLock)
    monkeypatch.setattr(launcher, "_start_lanes", lambda lanes: {"ingestion": _FakeProcess()})
    monkeypatch.setattr(launcher, "record_lane_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_bool_env", lambda name, default: False)

    assert launcher.run_launcher() == 1
    launcher.STOP_EVENT.clear()


def test_run_launcher_watchdog_failure_exits_for_render_restart(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    launcher.STOP_EVENT.clear()

    class _FakeLock:
        refresh_interval_seconds = 1000

        def acquire_with_retry(self, stop_event):  # noqa: ARG002
            return True

        def refresh(self):
            return True

        def release(self):
            return True

    class _FakeProcess:
        pid = 1
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):  # noqa: ARG002
            self.returncode = 0
            return 0

    monotonic_values = iter([0, 0, 0, 1000])
    monkeypatch.setattr(launcher.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "WorkerLeaderLock", _FakeLock)
    monkeypatch.setattr(launcher, "_start_lanes", lambda lanes: {"ingestion": _FakeProcess()})
    monkeypatch.setattr(launcher, "record_lane_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_stale_lanes", lambda *a, **k: ["ingestion"])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(monotonic_values, 32))

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            from datetime import datetime

            return datetime(2026, 5, 2, 10, 0, 0, tzinfo=tz)

    monkeypatch.setattr(launcher, "datetime", _FakeDateTime)
    monkeypatch.setattr(launcher, "_int_env", lambda name, default, minimum=1: 0 if "GRACE" in name else 30)

    assert launcher.run_launcher() == 1
    launcher.STOP_EVENT.clear()
