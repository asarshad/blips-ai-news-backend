"""Dedicated worker for consuming content outbox events continuously."""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Iterable

from app.core.logging import get_logger, setup_logging
from app.services.content_event_dispatcher import ContentEventDispatcher

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


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
        processed = dispatcher.process_pending(limit=max(1, int(batch_size)))
        if processed <= 0:
            resolved_stop_event.wait(timeout=max(0.1, float(poll_seconds)))
            continue
        logger.info(
            "[content_event_worker] processed=%s event_types=%s",
            processed,
            ",".join(tuple(event_types or ())) or "*",
        )

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
