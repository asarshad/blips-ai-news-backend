"""Scheduled task for dispatching content lifecycle outbox events."""

from __future__ import annotations

import os

from app.core.logging import get_logger
from app.scheduler.job_stats import log_job_start
from app.scheduler.runtime import (
    current_worker_memory_mb,
    memory_over_soft_limit,
    memory_soft_limit_mb,
)
from app.services.content_event_dispatcher import ContentEventDispatcher

logger = get_logger(__name__)


def run_content_event_dispatch_job() -> None:
    """Dispatch pending content lifecycle events."""
    if memory_over_soft_limit():
        logger.warning(
            "[content_events] SKIPPED - worker memory is above soft limit "
            "worker_memory_mb=%s soft_limit_mb=%s",
            current_worker_memory_mb(),
            memory_soft_limit_mb(),
        )
        return

    stats = log_job_start("content_events")
    try:
        limit = max(1, int(os.getenv("CONTENT_EVENT_SCHEDULED_BATCH_SIZE", "25")))
        processed = ContentEventDispatcher().process_pending(limit=limit)
        stats.items_processed = processed
        logger.info("[content_events] processed=%d", processed)
    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[content_events] Fatal error: %s", exc)
    finally:
        stats.complete()
        stats.log_summary()
