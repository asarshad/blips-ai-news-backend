"""Low-frequency maintenance lane for non-queue periodic work."""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.base import SessionLocal
from app.scheduler.runtime import (
    current_worker_memory_mb,
    memory_over_soft_limit,
    memory_soft_limit_mb,
)
from app.services.worker_lane_metrics import read_lane_heartbeats, record_lane_heartbeat

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


@dataclass
class LaneTask:
    name: str
    run: Callable[[], None]
    interval_seconds: int | None = None
    next_run_at: float = 0.0
    utc_hour: int | None = None
    utc_minute: int = 0


def _handle_stop(signum, _frame) -> None:
    logger.info("Received signal %s; stopping maintenance lane", signum)
    STOP_EVENT.set()


def _minutes_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


def _next_utc_time(hour: int, minute: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return time.monotonic() + (target - now).total_seconds()


def _run_event_backfill() -> None:
    db = SessionLocal()
    try:
        from app.services.content_event_backfill_service import enqueue_pending_content_events

        result = enqueue_pending_content_events(
            db,
            lookback_days=_minutes_env("CONTENT_EVENT_BACKFILL_LOOKBACK_DAYS", 7),
            limit=_minutes_env("CONTENT_EVENT_BACKFILL_LIMIT", 500),
            pending_only=True,
        )
        logger.info("[maintenance_lane] event backfill: %s", result)
    finally:
        db.close()


def _task_catalog() -> list[LaneTask]:
    signal_minutes = max(int(getattr(settings, "SIGNAL_INTERVAL_MINUTES", 60) or 60), 15)
    backfill_hours = _minutes_env("BACKFILL_INTERVAL_HOURS", 6)
    event_backfill_minutes = _minutes_env("CONTENT_EVENT_BACKFILL_INTERVAL_MINUTES", 10)

    from app.scheduler.tasks_backfill import run_backfill_job
    from app.scheduler.tasks_cleanup import run_data_cleanup_job
    from app.scheduler.tasks_curation import (
        run_preference_decay_job,
        run_scoring_job,
    )
    from app.scheduler.tasks_health import (
        check_ingestion_health,
        check_inventory_health,
        check_strategic_content_health,
    )
    from app.scheduler.tasks_major_news import run_major_news_probe_job
    from app.scheduler.tasks_promotion import run_promotion_job
    from app.scheduler.tasks_signals import run_signal_ingestion_job

    now = time.monotonic()
    major_news_minutes = _minutes_env("MAJOR_NEWS_PROBE_INTERVAL_MINUTES", 15, minimum=5)
    return [
        LaneTask("event_backfill", _run_event_backfill, event_backfill_minutes * 60, now + 15),
        LaneTask("promotion_sweep", run_promotion_job, 5 * 60, now + 2 * 60),
        LaneTask("major_news_probe", run_major_news_probe_job, major_news_minutes * 60, now + 90),
        LaneTask("scoring", run_scoring_job, 60 * 60, now + 5 * 60),
        LaneTask("signal_ingestion", run_signal_ingestion_job, signal_minutes * 60, now + 6 * 60),
        LaneTask("backfill", run_backfill_job, backfill_hours * 60 * 60, now + 10 * 60),
        LaneTask("ingestion_health", check_ingestion_health, 30 * 60, now + 3 * 60),
        LaneTask("inventory_health", check_inventory_health, 30 * 60, now + 4 * 60),
        LaneTask("strategic_content_health", check_strategic_content_health, 30 * 60, now + 5 * 60),
        LaneTask("preference_decay", run_preference_decay_job, utc_hour=3, utc_minute=0),
        LaneTask("data_cleanup", run_data_cleanup_job, utc_hour=4, utc_minute=0),
    ]


def _schedule_next(task: LaneTask) -> None:
    if task.utc_hour is not None:
        task.next_run_at = _next_utc_time(task.utc_hour, task.utc_minute)
    elif task.interval_seconds is not None:
        task.next_run_at = time.monotonic() + task.interval_seconds
    else:
        task.next_run_at = time.monotonic() + 3600


def _heartbeat_interval_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("WORKER_LANE_HEARTBEAT_SECONDS", "60")))
    except ValueError:
        return 60.0


def _parse_heartbeat_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lane_recently_running(lane_name: str, *, max_age_seconds: int = 180) -> bool:
    snapshot = read_lane_heartbeats()
    if not snapshot.get("available"):
        return False
    now = datetime.now(timezone.utc)
    for row in snapshot.get("lanes", []):
        if not isinstance(row, dict) or row.get("lane") != lane_name:
            continue
        status = str(row.get("status") or "").strip().lower()
        updated_at = _parse_heartbeat_time(row.get("updated_at"))
        if updated_at is None:
            return False
        age_seconds = (now - updated_at).total_seconds()
        return status == "running" and age_seconds <= max_age_seconds
    return False


def _defer_seconds_for_task(task: LaneTask) -> int | None:
    if memory_over_soft_limit():
        logger.warning(
            "[maintenance_lane] deferring task=%s due to worker memory "
            "worker_memory_mb=%s soft_limit_mb=%s",
            task.name,
            current_worker_memory_mb(),
            memory_soft_limit_mb(),
        )
        return 60

    if task.name in {
        "backfill",
        "scoring",
        "inventory_health",
        "major_news_probe",
    } and _lane_recently_running(
        "ingestion"
    ):
        logger.info(
            "[maintenance_lane] deferring task=%s while ingestion lane is running",
            task.name,
        )
        return 120

    return None


def _start_background_heartbeat(current_status: dict) -> threading.Thread:
    """Emit maintenance heartbeats on a fixed cadence regardless of task duration."""
    interval = _heartbeat_interval_seconds()

    def _loop() -> None:
        while not STOP_EVENT.wait(timeout=interval):
            try:
                record_lane_heartbeat(
                    "maintenance",
                    status=current_status.get("status", "alive"),
                    details=current_status.get("details"),
                )
            except Exception:
                pass

    thread = threading.Thread(target=_loop, name="maintenance-heartbeat", daemon=True)
    thread.start()
    return thread


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    tasks = _task_catalog()
    for task in tasks:
        if task.utc_hour is not None:
            _schedule_next(task)

    logger.info("Starting maintenance lane with tasks=%s", [task.name for task in tasks])
    record_lane_heartbeat(
        "maintenance",
        status="starting",
        details={"tasks": [task.name for task in tasks]},
    )
    current_status: dict = {
        "status": "starting",
        "details": {"tasks": [task.name for task in tasks]},
    }
    _start_background_heartbeat(current_status)

    while not STOP_EVENT.is_set():
        now = time.monotonic()
        due = [task for task in tasks if task.next_run_at <= now]
        if not due:
            next_due = min(task.next_run_at for task in tasks)
            STOP_EVENT.wait(timeout=min(5.0, max(0.1, next_due - now)))
            continue

        for task in sorted(due, key=lambda item: item.next_run_at):
            if STOP_EVENT.is_set():
                break
            defer_seconds = _defer_seconds_for_task(task)
            if defer_seconds is not None:
                task.next_run_at = time.monotonic() + defer_seconds
                current_status["status"] = "deferred"
                current_status["details"] = {
                    "task": task.name,
                    "defer_seconds": defer_seconds,
                }
                record_lane_heartbeat(
                    "maintenance",
                    status="deferred",
                    details={"task": task.name, "defer_seconds": defer_seconds},
                )
                continue
            started = time.monotonic()
            current_status["status"] = "running"
            current_status["details"] = {"task": task.name}
            record_lane_heartbeat("maintenance", status="running", details={"task": task.name})
            try:
                logger.info("[maintenance_lane] starting task=%s", task.name)
                task.run()
                duration = round(time.monotonic() - started, 2)
                logger.info(
                    "[maintenance_lane] finished task=%s duration_seconds=%s",
                    task.name,
                    duration,
                )
                current_status["status"] = "idle"
                current_status["details"] = {
                    "last_task": task.name,
                    "last_duration_seconds": duration,
                }
                record_lane_heartbeat(
                    "maintenance",
                    status="idle",
                    details={"last_task": task.name, "last_duration_seconds": duration},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("[maintenance_lane] task failed task=%s: %s", task.name, exc)
                current_status["status"] = "error"
                current_status["details"] = {"task": task.name, "error": str(exc)}
                record_lane_heartbeat(
                    "maintenance",
                    status="error",
                    details={"task": task.name, "error": str(exc)},
                )
            finally:
                _schedule_next(task)

    record_lane_heartbeat("maintenance", status="stopped")
    logger.info("Maintenance lane stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
