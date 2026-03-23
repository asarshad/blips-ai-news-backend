"""Scheduled task for dispatching content lifecycle outbox events."""

from __future__ import annotations

from app.core.logging import get_logger
from app.scheduler.job_stats import log_job_start
from app.services.content_event_dispatcher import ContentEventDispatcher

logger = get_logger(__name__)


def run_content_event_dispatch_job() -> None:
    """Dispatch pending content lifecycle events."""
    stats = log_job_start("content_events")
    try:
        processed = ContentEventDispatcher().process_pending(limit=100)
        stats.items_processed = processed
        logger.info("[content_events] processed=%d", processed)
    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[content_events] Fatal error: %s", exc)
    finally:
        stats.complete()
        stats.log_summary()
