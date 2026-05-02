"""CLI entrypoint for one durable content-event lane."""

from __future__ import annotations

import argparse
import os
import signal
import threading

from app.content_event_worker import run_content_event_worker
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

STOP_EVENT = threading.Event()


def _handle_stop(signum, _frame) -> None:
    logger.info("Received signal %s; stopping content event lane", signum)
    STOP_EVENT.set()


def _parse_event_types(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one content-event worker lane")
    parser.add_argument("--lane-name", required=True)
    parser.add_argument("--event-types", required=True)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--poll-seconds", type=float, default=None)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    event_types = _parse_event_types(args.event_types)
    default_batch = int(os.getenv("CONTENT_EVENT_BATCH_SIZE", "10"))
    ai_batch = int(os.getenv("CONTENT_EVENT_AI_BATCH_SIZE", "2"))
    batch_size = args.batch_size
    if batch_size is None:
        batch_size = ai_batch if "content.ai_summary.requested" in event_types else default_batch
    poll_seconds = (
        args.poll_seconds
        if args.poll_seconds is not None
        else float(os.getenv("CONTENT_EVENT_POLL_SECONDS", "2.0"))
    )

    return run_content_event_worker(
        event_types=event_types,
        batch_size=max(1, int(batch_size)),
        poll_seconds=max(0.1, float(poll_seconds)),
        stop_event=STOP_EVENT,
        lane_name=args.lane_name,
    )


if __name__ == "__main__":
    raise SystemExit(main())

