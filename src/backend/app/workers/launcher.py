"""Process launcher for the single Render worker service."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger, setup_logging
from app.services.worker_lane_metrics import read_lane_heartbeats, record_lane_heartbeat
from app.workers.leader_lock import WorkerLeaderLock

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


@dataclass(frozen=True)
class LaneSpec:
    name: str
    module: str
    args: tuple[str, ...] = ()


DEFAULT_LANES: tuple[LaneSpec, ...] = (
    LaneSpec("ingestion", "app.workers.ingestion_lane"),
    LaneSpec(
        "promotion",
        "app.workers.content_event_lane",
        (
            "--lane-name",
            "promotion",
            "--event-types",
            "content.promotion_eval.requested",
        ),
    ),
    LaneSpec(
        "ai_summary",
        "app.workers.content_event_lane",
        (
            "--lane-name",
            "ai_summary",
            "--event-types",
            "content.ai_summary.requested",
        ),
    ),
    LaneSpec(
        "article_images",
        "app.workers.content_event_lane",
        (
            "--lane-name",
            "article_images",
            "--event-types",
            "article.image_verification.requested",
        ),
    ),
    LaneSpec(
        "ready_events",
        "app.workers.content_event_lane",
        (
            "--lane-name",
            "ready_events",
            "--event-types",
            "content.ready,content.unready",
        ),
    ),
    LaneSpec(
        "clustering",
        "app.workers.content_event_lane",
        (
            "--lane-name",
            "clustering",
            "--event-types",
            "content.clustering.requested",
        ),
    ),
    LaneSpec("maintenance", "app.workers.maintenance_lane"),
)


def _handle_stop(signum, _frame) -> None:
    logger.info("Received signal %s; stopping worker launcher", signum)
    STOP_EVENT.set()


def _child_env(lane: LaneSpec) -> dict[str, str]:
    env = os.environ.copy()
    env["WORKER_LANE_NAME"] = lane.name
    env["DB_POOL_SIZE"] = os.getenv("LANE_DB_POOL_SIZE", os.getenv("DB_POOL_SIZE", "1"))
    env["DB_MAX_OVERFLOW"] = os.getenv(
        "LANE_DB_MAX_OVERFLOW",
        os.getenv("DB_MAX_OVERFLOW", "1"),
    )
    return env


def _command_for_lane(lane: LaneSpec) -> list[str]:
    return [sys.executable, "-u", "-m", lane.module, *lane.args]


def _start_lanes(lanes: tuple[LaneSpec, ...]) -> dict[str, subprocess.Popen]:
    processes: dict[str, subprocess.Popen] = {}
    for lane in lanes:
        command = _command_for_lane(lane)
        logger.info("Starting worker lane %s: %s", lane.name, " ".join(command))
        try:
            processes[lane.name] = subprocess.Popen(command, env=_child_env(lane))  # noqa: S603
        except Exception:
            _stop_lanes(processes)
            raise
        record_lane_heartbeat(
            lane.name,
            status="launched",
            details={"pid": processes[lane.name].pid},
        )
    return processes


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


def _parse_heartbeat_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stale_lanes(
    expected_lanes: set[str],
    *,
    stale_after_seconds: int,
    startup_grace_seconds: int,
    launched_at: float,
) -> list[str]:
    """Return lane names whose heartbeat is missing or stale."""
    if time.monotonic() - launched_at < startup_grace_seconds:
        return []

    snapshot = read_lane_heartbeats()
    if not snapshot.get("available"):
        logger.warning("Worker lane heartbeat snapshot unavailable: %s", snapshot.get("error"))
        return []

    now = datetime.now(timezone.utc)
    by_lane = {
        str(row.get("lane")): row
        for row in snapshot.get("lanes", [])
        if isinstance(row, dict) and row.get("lane")
    }
    stale: list[str] = []
    for lane_name in sorted(expected_lanes):
        payload = by_lane.get(lane_name)
        if payload is None:
            stale.append(lane_name)
            continue
        updated_at = _parse_heartbeat_time(payload.get("updated_at"))
        if updated_at is None:
            stale.append(lane_name)
            continue
        age_seconds = (now - updated_at).total_seconds()
        if age_seconds > stale_after_seconds:
            stale.append(lane_name)
    return stale


def _restart_lane(
    processes: dict[str, subprocess.Popen],
    lane_name: str,
    *,
    grace_seconds: float = 10.0,
) -> bool:
    """Terminate one lane process and start a fresh one in its place."""
    lane = next((spec for spec in DEFAULT_LANES if spec.name == lane_name), None)
    if lane is None:
        return False

    process = processes.get(lane_name)
    if process is not None and process.poll() is None:
        logger.warning("Restarting stale worker lane %s pid=%s", lane_name, process.pid)
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Killing unresponsive stale worker lane %s pid=%s",
                lane_name,
                process.pid,
            )
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    command = _command_for_lane(lane)
    try:
        processes[lane_name] = subprocess.Popen(command, env=_child_env(lane))  # noqa: S603
    except Exception:
        logger.exception("Failed to restart stale worker lane %s", lane_name)
        return False
    record_lane_heartbeat(
        lane_name,
        status="restarted",
        details={"pid": processes[lane_name].pid, "reason": "watchdog_stale"},
    )
    return True


def _stop_lanes(processes: dict[str, subprocess.Popen], *, grace_seconds: float = 20.0) -> None:
    """Terminate every lane with an independent grace window."""
    for name, process in processes.items():
        if process.poll() is None:
            logger.info("Terminating worker lane %s pid=%s", name, process.pid)
            process.terminate()

    for name, process in processes.items():
        if process.poll() is not None:
            logger.info("Worker lane %s exited code=%s", name, process.returncode)
            continue
        try:
            process.wait(timeout=grace_seconds)
            logger.info("Worker lane %s exited code=%s", name, process.returncode)
        except subprocess.TimeoutExpired:
            logger.warning("Killing unresponsive worker lane %s pid=%s", name, process.pid)
            process.kill()
            process.wait(timeout=5)


def run_launcher() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if os.getenv("SCHEDULER_ENABLED", "true").lower() not in {"true", "1", "yes", "on"}:
        logger.info("Worker launcher disabled via SCHEDULER_ENABLED=false; idling")
        while not STOP_EVENT.is_set():
            STOP_EVENT.wait(timeout=3600)
        return 0

    lock = WorkerLeaderLock()
    if not lock.acquire_with_retry(STOP_EVENT):
        if STOP_EVENT.is_set():
            return 0
        logger.error("Could not acquire worker leader lock; exiting for restart")
        return 1

    logger.info("Worker leader lock acquired; launching lanes")
    processes: dict[str, subprocess.Popen] = {}
    exit_code = 0
    redis_failures = 0
    launched_at = time.monotonic()
    next_lock_refresh = time.monotonic() + lock.refresh_interval_seconds
    watchdog_enabled = _bool_env("WORKER_LANE_WATCHDOG_ENABLED", True)
    watchdog_interval_seconds = _int_env("WORKER_LANE_WATCHDOG_INTERVAL_SECONDS", 30)
    watchdog_stale_seconds = _int_env("WORKER_LANE_STALE_SECONDS", 900)
    watchdog_startup_grace_seconds = _int_env("WORKER_LANE_STARTUP_GRACE_SECONDS", 120)
    # Flap protection: if a lane keeps going stale we still want Render to
    # restart the whole worker rather than restart-loop forever in silence.
    flap_window_seconds = _int_env("WORKER_LANE_FLAP_WINDOW_SECONDS", 1800)
    flap_max_restarts = _int_env("WORKER_LANE_FLAP_MAX_RESTARTS", 5)
    lane_restart_history: dict[str, list[float]] = {}
    watchdog_grace_until = datetime.now(timezone.utc) + timedelta(
        seconds=watchdog_startup_grace_seconds
    )
    next_watchdog_check = time.monotonic() + watchdog_interval_seconds
    try:
        processes = _start_lanes(DEFAULT_LANES)
        record_lane_heartbeat(
            "launcher",
            status="running",
            details={"lanes": list(processes.keys())},
        )
        while not STOP_EVENT.is_set():
            for name, process in processes.items():
                code = process.poll()
                if code is None:
                    continue
                logger.error("Worker lane %s exited unexpectedly code=%s", name, code)
                record_lane_heartbeat(name, status="exited", details={"exit_code": code})
                exit_code = code or 1
                STOP_EVENT.set()
                break

            now = time.monotonic()
            if (
                watchdog_enabled
                and now >= next_watchdog_check
                and not STOP_EVENT.is_set()
                and datetime.now(timezone.utc) >= watchdog_grace_until
            ):
                stale = _stale_lanes(
                    set(processes.keys()),
                    stale_after_seconds=watchdog_stale_seconds,
                    startup_grace_seconds=watchdog_startup_grace_seconds,
                    launched_at=launched_at,
                )
                if stale:
                    # Don't blow up the whole worker for one stale lane —
                    # heartbeats can lag during long ingestion or maintenance
                    # work even when the process is alive. Restart just the
                    # affected lane(s); only escalate to a full worker exit
                    # if the lane process itself is already dead.
                    dead_lanes = [
                        name
                        for name in stale
                        if name in processes and processes[name].poll() is not None
                    ]
                    if dead_lanes:
                        logger.error(
                            "Worker lane(s) %s exited and stale; stopping for Render restart",
                            dead_lanes,
                        )
                        record_lane_heartbeat(
                            "launcher",
                            status="watchdog_failed",
                            details={"stale_lanes": stale, "dead_lanes": dead_lanes},
                        )
                        exit_code = 1
                        STOP_EVENT.set()
                    else:
                        # Flap protection: count restarts within the rolling
                        # window. If any lane exceeds the cap, escalate to a
                        # full worker exit so Render restarts us with backoff
                        # instead of letting the lane silently restart-loop.
                        cutoff = now - flap_window_seconds
                        flapping_lanes: list[str] = []
                        for lane_name in stale:
                            history = lane_restart_history.setdefault(lane_name, [])
                            history[:] = [t for t in history if t >= cutoff]
                            if len(history) >= flap_max_restarts:
                                flapping_lanes.append(lane_name)
                        if flapping_lanes:
                            logger.error(
                                "Worker lane(s) %s exceeded %s restarts in %ss; "
                                "stopping for Render restart",
                                flapping_lanes,
                                flap_max_restarts,
                                flap_window_seconds,
                            )
                            record_lane_heartbeat(
                                "launcher",
                                status="watchdog_failed",
                                details={
                                    "stale_lanes": stale,
                                    "flapping_lanes": flapping_lanes,
                                    "restart_window_seconds": flap_window_seconds,
                                },
                            )
                            exit_code = 1
                            STOP_EVENT.set()
                        else:
                            logger.warning(
                                "Worker lane heartbeat stale for lanes=%s; "
                                "restarting affected lanes",
                                stale,
                            )
                            record_lane_heartbeat(
                                "launcher",
                                status="watchdog_restart",
                                details={"stale_lanes": stale},
                            )
                            for lane_name in stale:
                                if _restart_lane(processes, lane_name):
                                    lane_restart_history.setdefault(lane_name, []).append(
                                        now
                                    )
                            # Reset grace so newly started lanes have time to
                            # register a heartbeat before another check fires.
                            watchdog_grace_until = datetime.now(timezone.utc) + timedelta(
                                seconds=watchdog_startup_grace_seconds
                            )
                next_watchdog_check = now + watchdog_interval_seconds

            if now >= next_lock_refresh and not STOP_EVENT.is_set():
                refreshed = lock.refresh()
                if refreshed is True:
                    redis_failures = 0
                    next_lock_refresh = now + lock.refresh_interval_seconds
                    record_lane_heartbeat("launcher", status="running")
                elif refreshed is None:
                    redis_failures += 1
                    logger.warning("Worker lock refresh failed (%s/5)", redis_failures)
                    watchdog_grace_until = datetime.now(timezone.utc) + timedelta(
                        seconds=watchdog_startup_grace_seconds
                    )
                    if redis_failures >= 5:
                        logger.error("Redis lock refresh failed repeatedly; stopping lanes")
                        exit_code = 1
                        STOP_EVENT.set()
                    next_lock_refresh = now + lock.refresh_interval_seconds
                else:
                    logger.error("Worker leader lock lost; stopping lanes")
                    exit_code = 1
                    STOP_EVENT.set()

            STOP_EVENT.wait(timeout=1.0)
    finally:
        logger.info("Worker launcher stopping lanes")
        _stop_lanes(processes)
        if lock.release():
            logger.info("Worker leader lock released")
        else:
            logger.info("Worker leader lock not released; already lost or unavailable")
        record_lane_heartbeat("launcher", status="stopped", details={"exit_code": exit_code})
    return exit_code


def main() -> int:
    return run_launcher()


if __name__ == "__main__":
    raise SystemExit(main())
