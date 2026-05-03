"""Dedicated worker for consuming content outbox events continuously."""

from __future__ import annotations

import gc
import os
import signal
import threading
import time
from typing import Iterable

from app.core.logging import get_logger, setup_logging
from app.scheduler.runtime import (
    current_worker_memory_mb,
    memory_over_soft_limit,
    memory_soft_limit_mb,
)
from app.services.content_event_dispatcher import ContentEventDispatcher
from app.services.worker_lane_metrics import record_lane_heartbeat

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


def _log_memory_snapshot(label: str) -> None:
    """Emit lightweight RSS telemetry for diagnosing worker OOM pressure."""
    try:
        from app.core.observability import get_process_runtime_stats

        stats = get_process_runtime_stats()
        logger.info(
            "[content_event_worker:%s] process_memory rss_mb=%s peak_rss_mb=%s "
            "container_mb=%s container_limit_mb=%s uptime_seconds=%s",
            label,
            stats.get("rss_mb"),
            stats.get("peak_rss_mb"),
            stats.get("container_memory_mb"),
            stats.get("container_memory_limit_mb"),
            stats.get("uptime_seconds"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to capture content event worker memory snapshot: %s", exc)


def _pause_for_shared_worker_pressure(
    *,
    event_types: tuple[str, ...],
    stop_event: threading.Event,
    poll_seconds: float,
) -> bool:
    """Back off only for concrete resource pressure."""
    label = ",".join(event_types) or "*"
    if memory_over_soft_limit():
        gc.collect()
        memory_mb = current_worker_memory_mb()
        logger.warning(
            "[content_event_worker] memory throttle worker_memory_mb=%s soft_limit_mb=%s "
            "event_types=%s",
            memory_mb,
            memory_soft_limit_mb(),
            label,
        )
        stop_event.wait(timeout=max(5.0, float(poll_seconds)))
        return True

    return False


def _handle_stop(_signum, _frame) -> None:
    STOP_EVENT.set()


def install_signal_handlers() -> None:
    """Install best-effort signal handlers for graceful shutdown."""
    try:
        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
    except Exception:
        return


def resolve_event_types(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated event type list from the environment."""
    if raw is None:
        return ()
    values = [part.strip() for part in raw.split(",")]
    return tuple(part for part in values if part)


def run_content_event_worker(
    *,
    event_types: Iterable[str] | None = None,
    batch_size: int = 50,
    poll_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
    lane_name: str | None = None,
) -> int:
    """Run a continuous outbox-consumer loop."""
    resolved_stop_event = stop_event or STOP_EVENT
    event_type_tuple = tuple(event_types or ())
    heartbeat_name = lane_name or f"events:{','.join(event_type_tuple) or 'all'}"
    dispatcher = ContentEventDispatcher(
        event_types=event_type_tuple,
    )
    logger.info(
        "Starting content event worker event_types=%s batch_size=%s poll_seconds=%s",
        ",".join(event_type_tuple) or "*",
        batch_size,
        poll_seconds,
    )
    record_lane_heartbeat(
        heartbeat_name,
        status="starting",
        details={"event_types": event_type_tuple, "batch_size": batch_size},
    )
    last_heartbeat = 0.0

    while not resolved_stop_event.is_set():
        if _pause_for_shared_worker_pressure(
            event_types=event_type_tuple,
            stop_event=resolved_stop_event,
            poll_seconds=poll_seconds,
        ):
            record_lane_heartbeat(
                heartbeat_name,
                status="throttled",
                details={"event_types": event_type_tuple},
            )
            continue

        processed = dispatcher.process_pending(limit=max(1, int(batch_size)))
        now = time.monotonic()
        if processed > 0 or now - last_heartbeat >= 60:
            record_lane_heartbeat(
                heartbeat_name,
                status="idle" if processed <= 0 else "processed",
                details={"event_types": event_type_tuple, "processed": processed},
            )
            last_heartbeat = now
        if processed <= 0:
            resolved_stop_event.wait(timeout=max(0.1, float(poll_seconds)))
            continue
        logger.info(
            "[content_event_worker] processed=%s event_types=%s",
            processed,
            ",".join(event_type_tuple) or "*",
        )
        gc.collect()
        _log_memory_snapshot(",".join(event_type_tuple) or "all")

    logger.info("Content event worker stopping")
    record_lane_heartbeat(
        heartbeat_name,
        status="stopped",
        details={"event_types": event_type_tuple},
    )
    return 0


def main() -> int:
    install_signal_handlers()
    event_types = resolve_event_types(os.getenv("CONTENT_EVENT_TYPES"))
    batch_size = int(os.getenv("CONTENT_EVENT_BATCH_SIZE", "50"))
    poll_seconds = float(os.getenv("CONTENT_EVENT_POLL_SECONDS", "1.0"))
    return run_content_event_worker(
        event_types=event_types,
        batch_size=batch_size,
        poll_seconds=poll_seconds,
        lane_name=os.getenv("WORKER_LANE_NAME"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
