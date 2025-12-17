"""
Scheduler module for background task scheduling.

Uses APScheduler to run periodic tasks like news fetching.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.scheduler.tasks import fetch_and_process_news, fetch_and_process_videos

logger = get_logger(__name__)


def init_scheduler() -> BackgroundScheduler | None:
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
        
        scheduler.start()
        logger.info(f"Started background scheduler - fetching news every {settings.NEWS_FETCH_INTERVAL_HOURS} hours")
        
        return scheduler
        
    except Exception as e:
        logger.error(f"Error initializing scheduler: {str(e)}")
        return None


__all__ = ["init_scheduler", "fetch_and_process_news", "fetch_and_process_videos"]
