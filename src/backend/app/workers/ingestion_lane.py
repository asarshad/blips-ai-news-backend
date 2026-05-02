"""Async ingestion lane.

This lane is a producer: it fetches healthy sources, inserts content, and
queues durable follow-up events. It does not run AI, image verification, push,
or feed-cache refresh inline.
"""

from __future__ import annotations

import os
import signal
import threading
import time

from app.core.logging import get_logger, setup_logging
from app.scheduler.tasks_ingestion import fetch_and_process_news
from app.services.worker_lane_metrics import record_lane_heartbeat

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


def _handle_stop(signum, _frame) -> None:
    logger.info("Received signal %s; stopping ingestion lane", signum)
    STOP_EVENT.set()
    try:
        from app.ingestion.checkpointing import STOP_EVENT as INGESTION_STOP_EVENT

        INGESTION_STOP_EVENT.set()
    except Exception:
        pass


def _bounded_ingestion_minutes() -> int:
    raw = os.getenv("INGESTION_SCHEDULER_MINUTES", os.getenv("NEWS_FETCH_INTERVAL_MINUTES", "15"))
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 15
    return max(5, min(15, minutes))


def _apply_recurring_timebox_default() -> None:
    """Prefer short recurring slices over all-cycle catch-up monopolies."""
    configured = os.getenv("INGESTION_RECURRING_MAX_SECONDS")
    if configured and not os.getenv("INGEST_CATCHUP_MAX_SECONDS"):
        os.environ["INGEST_CATCHUP_MAX_SECONDS"] = configured


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    _apply_recurring_timebox_default()

    interval_seconds = _bounded_ingestion_minutes() * 60
    logger.info(
        "Starting ingestion lane interval_seconds=%s catchup_max_seconds=%s",
        interval_seconds,
        os.getenv("INGEST_CATCHUP_MAX_SECONDS", "600"),
    )
    record_lane_heartbeat(
        "ingestion",
        status="starting",
        details={"interval_seconds": interval_seconds},
    )

    next_run = 0.0
    while not STOP_EVENT.is_set():
        now = time.monotonic()
        if now < next_run:
            STOP_EVENT.wait(timeout=min(5.0, next_run - now))
            continue

        started = time.monotonic()
        record_lane_heartbeat("ingestion", status="running")
        try:
            fetch_and_process_news()
            duration = round(time.monotonic() - started, 2)
            record_lane_heartbeat(
                "ingestion",
                status="idle",
                details={"last_duration_seconds": duration},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ingestion_lane] fetch failed: %s", exc)
            record_lane_heartbeat("ingestion", status="error", details={"error": str(exc)})

        next_run = time.monotonic() + interval_seconds

    record_lane_heartbeat("ingestion", status="stopped")
    logger.info("Ingestion lane stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

