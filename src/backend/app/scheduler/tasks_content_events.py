"""Scheduled task for dispatching content lifecycle outbox events."""

from __future__ import annotations

from app.core.logging import get_logger
from app.scheduler.job_stats import log_job_start
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
from app.services.content_readiness import CONTENT_READY_EVENT_TYPE, CONTENT_UNREADY_EVENT_TYPE
from app.services.content_event_dispatcher import ContentEventDispatcher

logger = get_logger(__name__)


def run_content_event_dispatch_job() -> None:
    """Dispatch lightweight ready/unready lifecycle events."""
    stats = log_job_start("content_events")
    try:
        processed = ContentEventDispatcher(
            event_types=(CONTENT_READY_EVENT_TYPE, CONTENT_UNREADY_EVENT_TYPE),
        ).process_pending(limit=100)
        stats.items_processed = processed
        logger.info("[content_events] processed=%d", processed)
    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[content_events] Fatal error: %s", exc)
    finally:
        stats.complete()
        stats.log_summary()


def run_article_image_event_dispatch_job() -> None:
    """Dispatch queued article image verification work on its own worker lane."""
    stats = log_job_start("article_image_events")
    try:
        processed = ContentEventDispatcher(
            event_types=(ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,),
        ).process_pending(limit=100)
        stats.items_processed = processed
        logger.info("[article_image_events] processed=%d", processed)
    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[article_image_events] Fatal error: %s", exc)
    finally:
        stats.complete()
        stats.log_summary()
