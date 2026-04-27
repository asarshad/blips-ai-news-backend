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
    FETCH_NEWS_JOB,
    current_rss_mb,
    is_job_active,
    memory_over_soft_limit,
    memory_soft_limit_mb,
)
from app.services.content_event_dispatcher import ContentEventDispatcher

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()
PROMOTION_EVENT_TYPE = "content.promotion_eval.requested"


def _log_memory_snapshot(label: str) -> None:
    """Emit lightweight RSS telemetry for diagnosing worker OOM pressure."""
    try:
        from app.core.observability import get_process_runtime_stats

        stats = get_process_runtime_stats()
        logger.info(
            "[content_event_worker:%s] process_memory rss_mb=%s peak_rss_mb=%s uptime_seconds=%s",
            label,
            stats.get("rss_mb"),
            stats.get("peak_rss_mb"),
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
    """Back off when the single Render worker is busy or near its memory budget."""
    label = ",".join(event_types) or "*"
    fetch_pause_exempt = event_types == (PROMOTION_EVENT_TYPE,)
    if os.getenv("CONTENT_EVENT_PAUSE_DURING_FETCH", "true").lower() in {
        "true",
        "1",
        "yes",
        "on",
    } and not fetch_pause_exempt and is_job_active(FETCH_NEWS_JOB):
        logger.info(
            "[content_event_worker] paused while fetch_news is active event_types=%s",
            label,
        )
        stop_event.wait(timeout=max(1.0, float(poll_seconds)))
        return True

    if memory_over_soft_limit():
        gc.collect()
        rss_mb = current_rss_mb()
        logger.warning(
            "[content_event_worker] memory throttle rss_mb=%s soft_limit_mb=%s event_types=%s",
            rss_mb,
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
) -> int:
    """Run a continuous outbox-consumer loop."""
    resolved_stop_event = stop_event or STOP_EVENT
    dispatcher = ContentEventDispatcher(
        event_types=tuple(event_types or ()),
    )
    logger.info(
        "Starting content event worker event_types=%s batch_size=%s poll_seconds=%s",
        ",".join(tuple(event_types or ())) or "*",
        batch_size,
        poll_seconds,
    )

    while not resolved_stop_event.is_set():
        event_type_tuple = tuple(event_types or ())
        if _pause_for_shared_worker_pressure(
            event_types=event_type_tuple,
            stop_event=resolved_stop_event,
            poll_seconds=poll_seconds,
        ):
            continue

        processed = dispatcher.process_pending(limit=max(1, int(batch_size)))
        if processed <= 0:
            resolved_stop_event.wait(timeout=max(0.1, float(poll_seconds)))
            continue
        logger.info(
            "[content_event_worker] processed=%s event_types=%s",
            processed,
            ",".join(tuple(event_types or ())) or "*",
        )
        gc.collect()
        _log_memory_snapshot(",".join(tuple(event_types or ())) or "all")

    logger.info("Content event worker stopping")
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
