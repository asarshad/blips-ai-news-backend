
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
    """Scheduled task to fetch, summarize, and store new articles"""
    logger.info("Starting scheduled news fetch and processing")
    
    redis_client = get_redis()
    db = SessionLocal()
    try:
        # Create repositories
        article_repo = ArticleRepository(db)
        video_repo = VideoRepository(db)
        
        # Fetch new articles
        news_fetcher = NewsFetcher(article_repo)
        articles = news_fetcher.fetch_latest_articles()
        
        if not articles:
            logger.info("No new articles found")
        else:
            logger.info(f"Found {len(articles)} new articles to process")
            
            # Summarize and save articles
            summarizer = ArticleSummarizer(article_repo)
            
            for article_data in articles:
                try:
                    # Summarize article
                    processed_article = summarizer.summarize_article(article_data)
                    
                    # Save to database
                    summarizer.save_article(processed_article)
                    
                except Exception as e:
                    logger.error(f"Error processing article {article_data.get('title')}: {str(e)}")
            
            # Update article cache
            article_service = ArticleService(article_repo, redis_client)
            article_service.cache_articles()
        
        # Fetch videos
        fetch_and_process_videos_task(video_repo)
        
        logger.info("Completed news fetch and processing")
        
    except Exception as e:
        logger.error(f"Error in news fetch and processing task: {str(e)}")
    finally:
        db.close()


def fetch_and_process_videos_task(video_repo: VideoRepository = None):
    """Fetch and store new videos from YouTube channels"""
    logger.info("Starting video fetch")
    
    db = None
    close_db = False
    if video_repo is None:
        db = SessionLocal()
        video_repo = VideoRepository(db)
        close_db = True
    
    try:
        video_fetcher = VideoFetcher(video_repo)
        videos = video_fetcher.fetch_latest_videos()
        
        if not videos:
            logger.info("No new videos found")
            return
        
        saved_count = video_fetcher.save_videos(videos)
        logger.info(f"Saved {saved_count} new videos")
        
    except Exception as e:
        logger.error(f"Error in video fetch task: {str(e)}")
    finally:
        if close_db and db:
            db.close()
