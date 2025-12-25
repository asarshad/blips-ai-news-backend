"""
Scheduler module for background task scheduling.

Uses APScheduler to run periodic tasks like news fetching.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.scheduler.tasks import (
    fetch_and_process_news,
    fetch_and_process_videos,
    run_scoring_job,
    run_clustering_job,
    run_preference_decay_job,
    run_backfill_job,
    retry_ai_processing,
)

logger = get_logger(__name__)


def init_scheduler() -> Optional[BackgroundScheduler]:
    """
    Initialize and start the background scheduler.
    
    Returns:
        BackgroundScheduler instance, or None if initialization fails
    """
    try:
        scheduler = BackgroundScheduler()
        
        # Add job for news and video fetching
        scheduler.add_job(
            fetch_and_process_news,
            IntervalTrigger(hours=settings.NEWS_FETCH_INTERVAL_HOURS),
            id="fetch_news",
            replace_existing=True
        )
        
        # Add scoring job (hourly)
        scheduler.add_job(
            run_scoring_job,
            IntervalTrigger(hours=1),
            id="scoring_job",
            replace_existing=True
        )
        
        # Add clustering job (every 15 minutes)
        scheduler.add_job(
            run_clustering_job,
            IntervalTrigger(minutes=15),
            id="clustering_job",
            replace_existing=True
        )
        
        # Add preference decay job (daily at 3 AM)
        scheduler.add_job(
            run_preference_decay_job,
            CronTrigger(hour=3, minute=0),
            id="preference_decay_job",
            replace_existing=True
        )
        
        # Add AI processing retry job (every 2 hours)
        scheduler.add_job(
            retry_ai_processing,
            IntervalTrigger(hours=2),
            id="ai_retry_job",
            replace_existing=True
        )
        
        scheduler.start()
        logger.info(f"Started background scheduler - fetching news every {settings.NEWS_FETCH_INTERVAL_HOURS} hours")
        logger.info("Curation jobs: scoring (hourly), clustering (15min), decay (daily), AI retry (2h)")
        
        return scheduler
        
    except Exception as e:
        logger.error(f"Error initializing scheduler: {str(e)}")
        return None


    "retry_ai_processing",
__all__ = [
    "init_scheduler",
    "fetch_and_process_news",
    "fetch_and_process_videos",
    "run_scoring_job",
    "run_clustering_job",
    "run_preference_decay_job",
    "run_backfill_job",
]
