"""
Scheduler module for background task scheduling.

Uses APScheduler to run periodic tasks like news fetching.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.scheduler.tasks import (
    check_ingestion_health,
    check_inventory_health,
    fetch_and_process_news,
    retry_ai_processing,
    run_article_image_verification_job,
    run_backfill_job,
    run_clustering_job,
    run_content_event_dispatch_job,
    run_data_cleanup_job,
    run_preference_decay_job,
    run_promotion_job,
    run_scoring_job,
    run_signal_ingestion_job,
)

logger = get_logger(__name__)


def _scheduler_event_listener(event) -> None:
    """Surface scheduler overlaps/misses/errors through the shared alerting channel."""
    try:
        from app.services.alerting_service import AlertSeverity, alert_scheduler_job_issue

        job_id = getattr(event, "job_id", "unknown")
        if event.code == EVENT_JOB_MAX_INSTANCES:
            alert_scheduler_job_issue(
                job_id=job_id,
                issue_type="max_instances",
                details="Job skipped because maximum running instances was reached",
                severity=AlertSeverity.WARNING,
            )
        elif event.code == EVENT_JOB_MISSED:
            alert_scheduler_job_issue(
                job_id=job_id,
                issue_type="missed_run",
                details="Scheduled job missed its intended run window",
                severity=AlertSeverity.WARNING,
            )
        elif event.code == EVENT_JOB_ERROR:
            detail = str(getattr(event, "exception", None) or "job raised exception")
            alert_scheduler_job_issue(
                job_id=job_id,
                issue_type="job_error",
                details=detail,
                severity=AlertSeverity.CRITICAL,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to emit scheduler event alert: %s", exc)


def _bounded_ingestion_minutes(value: int) -> int:
    """Clamp ingestion cadence to the supported continuous range (5-15 minutes)."""
    if value < 5:
        logger.warning(
            "Ingestion scheduler minutes %s is below minimum; clamping to 5 minutes", value
        )
        return 5
    if value > 15:
        logger.warning(
            "Ingestion scheduler minutes %s is above maximum; clamping to 15 minutes", value
        )
        return 15
    return value


def _resolve_ingestion_minutes() -> int:
    """Resolve ingestion schedule cadence with backward-compatible fallback."""
    # Preferred knob for continuous ingestion cadence.
    raw = os.getenv("INGESTION_SCHEDULER_MINUTES")
    if raw is not None:
        try:
            return _bounded_ingestion_minutes(int(raw))
        except ValueError:
            logger.warning(
                "Invalid INGESTION_SCHEDULER_MINUTES=%r; falling back to NEWS_FETCH_INTERVAL_MINUTES",
                raw,
            )

    # Backward-compatible fallback.
    legacy_minutes = int(getattr(settings, "NEWS_FETCH_INTERVAL_MINUTES", 0) or 0)
    if legacy_minutes > 0:
        return _bounded_ingestion_minutes(legacy_minutes)

    # Legacy hours are no longer suitable for continuous ingestion loops.
    legacy_hours = int(getattr(settings, "NEWS_FETCH_INTERVAL_HOURS", 0) or 0)
    if legacy_hours > 0:
        logger.warning(
            "NEWS_FETCH_INTERVAL_HOURS=%s is deprecated for ingestion cadence; defaulting to 15 minutes",
            legacy_hours,
        )

    return 15


def _resolve_backfill_hours() -> int:
    """Resolve the recurring backfill cadence with a safe default."""
    raw = os.getenv("BACKFILL_INTERVAL_HOURS")
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning(
                "Invalid BACKFILL_INTERVAL_HOURS=%r; defaulting to 6 hours",
                raw,
            )
    return 6


def _initial_fetch_next_run(minutes: int) -> datetime:
    """Delay the first scheduled fetch so startup's manual fetch can finish."""
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _initial_ai_retry_next_run(fetch_minutes: int) -> datetime:
    """Stagger the first scheduled AI retry away from the fetch cycle."""
    default_offset = 10 if fetch_minutes >= 15 else max(1, fetch_minutes - 1)
    raw = os.getenv("AI_RETRY_OFFSET_MINUTES", str(default_offset))
    try:
        offset_minutes = max(1, min(14, int(raw)))
    except ValueError:
        offset_minutes = default_offset
    return _initial_fetch_next_run(fetch_minutes) + timedelta(minutes=offset_minutes)


def init_scheduler() -> Optional[BackgroundScheduler]:
    """
    Initialize and start the background scheduler.

    Returns:
        BackgroundScheduler instance, or None if initialization fails
    """
    try:
        scheduler = BackgroundScheduler()

        # Continuous ingestion runs every 5-15 minutes.
        fetch_minutes = _resolve_ingestion_minutes()
        fetch_trigger = IntervalTrigger(minutes=fetch_minutes)
        fetch_human = f"{fetch_minutes} minutes"
        backfill_hours = _resolve_backfill_hours()

        # Add job for news and video fetching
        scheduler.add_job(
            fetch_and_process_news,
            fetch_trigger,
            id="fetch_news",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            next_run_time=_initial_fetch_next_run(fetch_minutes),
        )

        # Add scoring job (hourly)
        scheduler.add_job(
            run_scoring_job,
            IntervalTrigger(hours=1),
            id="scoring_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        # Add clustering job (every 15 minutes)
        scheduler.add_job(
            run_clustering_job,
            IntervalTrigger(minutes=15),
            id="clustering_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

        # Add preference decay job (daily at 3 AM)
        scheduler.add_job(
            run_preference_decay_job,
            CronTrigger(hour=3, minute=0),
            id="preference_decay_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

        # Add AI processing retry job (every 15 minutes - critical for feed freshness)
        scheduler.add_job(
            retry_ai_processing,
            IntervalTrigger(minutes=15),
            id="ai_retry_job",
            kwargs={"trigger": "scheduled"},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            next_run_time=_initial_ai_retry_next_run(fetch_minutes),
        )

        # Dedicated lightweight image-verification job. Runs independently
        # of ai_retry so blocked images (the dominant readiness blocker)
        # drain on a short cadence without waiting for LLM work.
        image_verification_minutes = max(
            1, int(settings.ARTICLE_IMAGE_VERIFICATION_INTERVAL_MINUTES)
        )
        scheduler.add_job(
            run_article_image_verification_job,
            IntervalTrigger(minutes=image_verification_minutes),
            id="article_image_verification_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

        _threads_enabled = os.getenv("CONTENT_EVENT_THREADS_ENABLED", "false").lower() in (
            "true", "1", "yes", "on"
        )
        if not _threads_enabled:
            scheduler.add_job(
                run_content_event_dispatch_job,
                IntervalTrigger(minutes=1),
                id="content_event_dispatch_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

        # Add data cleanup job (daily at 4 AM UTC)
        scheduler.add_job(
            run_data_cleanup_job,
            CronTrigger(hour=4, minute=0),
            id="data_cleanup_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

        # Revisit older content so recoverable article images do not linger blank.
        scheduler.add_job(
            run_backfill_job,
            IntervalTrigger(hours=backfill_hours),
            id="backfill_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )

        # Add ingestion health check (every 30 minutes)
        scheduler.add_job(
            check_ingestion_health,
            IntervalTrigger(minutes=30),
            id="ingestion_health_check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        scheduler.add_job(
            check_inventory_health,
            IntervalTrigger(minutes=30),
            id="inventory_health_check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        # ── Coverage Guarantee: signal ingestion (every 60 min by default) ──
        signal_minutes = int(getattr(settings, "SIGNAL_INTERVAL_MINUTES", 60) or 60)
        scheduler.add_job(
            run_signal_ingestion_job,
            IntervalTrigger(minutes=max(signal_minutes, 15)),
            id="signal_ingestion_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        # ── Quality Gate: promotion scoring (every 30 minutes) ──────────────
        scheduler.add_job(
            run_promotion_job,
            IntervalTrigger(minutes=30),
            id="promotion_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        scheduler.start()
        scheduler.add_listener(
            _scheduler_event_listener,
            EVENT_JOB_ERROR | EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED,
        )
        logger.info(f"Started background scheduler - fetching news every {fetch_human}")
        logger.info(
            "Curation jobs: scoring (hourly), clustering (15min), decay (daily), "
            f"AI retry (15min), image verification ({image_verification_minutes}min), "
            f"content-events (1min), cleanup (daily), backfill ({backfill_hours}h), health (30min), inventory (30min), "
            f"signals ({signal_minutes}min), promotion (30min)"
        )

        return scheduler

    except Exception as e:
        logger.error(f"Error initializing scheduler: {str(e)}")
        return None


__all__ = [
    "init_scheduler",
    "_resolve_ingestion_minutes",
    "_resolve_backfill_hours",
    "fetch_and_process_news",
    "run_scoring_job",
    "run_clustering_job",
    "run_content_event_dispatch_job",
    "run_data_cleanup_job",
    "run_preference_decay_job",
    "run_backfill_job",
    "retry_ai_processing",
    "run_signal_ingestion_job",
    "run_promotion_job",
]
