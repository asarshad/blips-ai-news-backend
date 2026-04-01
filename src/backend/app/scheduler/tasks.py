"""Scheduled tasks for Blips.

Public import surface: other modules import task functions from here.

Implementation is split into smaller modules to keep files manageable.
"""

from app.scheduler.tasks_ai_retry import retry_ai_processing
from app.scheduler.tasks_backfill import run_backfill_job
from app.scheduler.tasks_cleanup import run_data_cleanup_job
from app.scheduler.tasks_content_events import run_content_event_dispatch_job
from app.scheduler.tasks_curation import (
    run_clustering_job,
    run_preference_decay_job,
    run_scoring_job,
)
from app.scheduler.tasks_health import check_ingestion_health, check_inventory_health, get_ingestion_metrics
from app.scheduler.tasks_ingestion import fetch_and_process_news
from app.scheduler.tasks_promotion import run_promotion_job
from app.scheduler.tasks_signals import run_signal_ingestion_job

__all__ = [
    "check_ingestion_health",
    "check_inventory_health",
    "fetch_and_process_news",
    "get_ingestion_metrics",
    "retry_ai_processing",
    "run_backfill_job",
    "run_clustering_job",
    "run_content_event_dispatch_job",
    "run_data_cleanup_job",
    "run_preference_decay_job",
    "run_promotion_job",
    "run_scoring_job",
    "run_signal_ingestion_job",
]
