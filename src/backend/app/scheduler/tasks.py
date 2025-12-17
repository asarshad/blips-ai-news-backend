
"""Scheduled tasks for fetching news articles and videos."""

from app.services.news_fetcher import NewsFetcher
from app.services.summarizer import ArticleSummarizer
from app.services.article_service import ArticleService
from app.services.video_fetcher import VideoFetcher
from app.db.base import SessionLocal
from app.core.dependencies import get_redis
from app.core.logging import get_logger
from app.repositories.article_repo import ArticleRepository
from app.repositories.video_repo import VideoRepository

logger = get_logger(__name__)


def fetch_and_process_news():
    """Scheduled task to fetch, summarize, and store new articles."""
    logger.info("Starting scheduled news fetch and processing")
    
    redis_client = get_redis()
    db = SessionLocal()
    
    try:
        article_repo = ArticleRepository(db)
        
        # Fetch new articles
        news_fetcher = NewsFetcher(article_repo)
        articles = news_fetcher.fetch_latest_articles()
        
        if not articles:
            logger.info("No new articles found")
        else:
            logger.info(f"Found {len(articles)} new articles to process")
            _process_articles(db, article_repo, articles)
            
            # Update article cache
            article_service = ArticleService(article_repo, redis_client)
            article_service.cache_articles()
        
        # Fetch videos in a separate transaction
        _fetch_videos(db)
        
        logger.info("Completed news fetch and processing")
        
    except Exception as e:
        logger.error(f"Error in news fetch task: {str(e)}")
        db.rollback()
    finally:
        db.close()


def _process_articles(db, article_repo: ArticleRepository, articles: list):
    """Process and save articles with individual error handling."""
    summarizer = ArticleSummarizer(article_repo)
    
    for article_data in articles:
        try:
            processed_article = summarizer.summarize_article(article_data)
            summarizer.save_article(processed_article)
            db.commit()
        except Exception as e:
            logger.error(f"Error processing article '{article_data.get('title')}': {str(e)}")
            db.rollback()


def _fetch_videos(db):
    """Fetch and save videos with separate transaction handling."""
    try:
        video_repo = VideoRepository(db)
        video_fetcher = VideoFetcher(video_repo)
        videos = video_fetcher.fetch_latest_videos()
        
        if not videos:
            logger.info("No new videos found")
            return
        
        saved_count = video_fetcher.save_videos(videos)
        logger.info(f"Saved {saved_count} new videos")
        
    except Exception as e:
        logger.error(f"Error in video fetch task: {str(e)}")
        db.rollback()


def fetch_and_process_videos():
    """Standalone task for fetching videos (can be scheduled separately)."""
    logger.info("Starting video fetch")
    
    db = SessionLocal()
    try:
        _fetch_videos(db)
    finally:
        db.close()
