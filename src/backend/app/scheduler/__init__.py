
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.scheduler.tasks import fetch_and_process_news
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def init_scheduler():
    """Initialize and start the background scheduler"""
    try:
        scheduler = BackgroundScheduler()
        
        # Add job for news fetching using interval from settings
        scheduler.add_job(
            fetch_and_process_news,
            IntervalTrigger(hours=settings.NEWS_FETCH_INTERVAL_HOURS),
            id="fetch_news",
            replace_existing=True
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info(f"Started background scheduler - fetching news every {settings.NEWS_FETCH_INTERVAL_HOURS} hours")
        
        return scheduler
    except Exception as e:
        logger.error(f"Error initializing scheduler: {str(e)}")
        return None
