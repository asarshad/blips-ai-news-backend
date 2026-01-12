
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
        
        # Run curation ingestion after fetching
        _run_curation_ingestion(db)
        
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


def _run_curation_ingestion(db):
    """Run ingestion pipeline to populate content_items from articles/videos."""
    try:
        from app.services.ingestion_pipeline import create_ingestion_pipeline
        
        pipeline = create_ingestion_pipeline(db)
        stats = pipeline.run_backfill(hours_back=24, limit=500)
        
        logger.info(f"Curation ingestion: {stats}")
        
    except Exception as e:
        logger.error(f"Error in curation ingestion: {str(e)}")


def fetch_and_process_videos():
    """Standalone task for fetching videos (can be scheduled separately)."""
    logger.info("Starting video fetch")
    
    db = SessionLocal()
    try:
        _fetch_videos(db)
    finally:
        db.close()


# ============================================================================
# Curation System Tasks
# ============================================================================

def run_scoring_job():
    """
    Scheduled task to update content scores.
    
    Should run hourly to keep scores fresh.
    """
    logger.info("Starting scoring job")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.repositories.user_repo import InteractionEventRepository
        from app.services.scoring_service import ScoringService
        
        content_repo = ContentItemRepository(db)
        event_repo = InteractionEventRepository(db)
        
        scoring = ScoringService(content_repo, event_repo)
        stats = scoring.run_scoring_job(hours_back=72)
        
        logger.info(f"Scoring job complete: {stats}")
        
    except Exception as e:
        logger.error(f"Error in scoring job: {str(e)}")
    finally:
        db.close()


def run_clustering_job():
    """
    Scheduled task to cluster unclustered content.
    
    Should run every 15 minutes for fresh clustering.
    """
    logger.info("Starting clustering job")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.services.clustering_service import ClusteringService
        
        content_repo = ContentItemRepository(db)
        
        clustering = ClusteringService(content_repo)
        stats = clustering.run_clustering_job()
        
        logger.info(f"Clustering job complete: {stats}")
        
    except Exception as e:
        logger.error(f"Error in clustering job: {str(e)}")
    finally:
        db.close()


def run_preference_decay_job():
    """
    Scheduled task to decay user preferences.
    
    Should run daily to keep preferences fresh.
    """
    logger.info("Starting preference decay job")
    
    db = SessionLocal()
    
    try:
        from app.repositories.user_repo import (
            UserProfileRepository,
            UserPreferenceRepository,
            InteractionEventRepository
        )
        from app.repositories.content_repo import ContentItemRepository
        from app.services.personalization_service import PersonalizationService
        
        profile_repo = UserProfileRepository(db)
        preference_repo = UserPreferenceRepository(db)
        event_repo = InteractionEventRepository(db)
        content_repo = ContentItemRepository(db)
        
        personalization = PersonalizationService(
            profile_repo, preference_repo, event_repo, content_repo
        )
        
        stats = personalization.run_decay_job()
        logger.info(f"Preference decay job complete: {stats}")
        
    except Exception as e:
        logger.error(f"Error in preference decay job: {str(e)}")
    finally:
        db.close()


def run_backfill_job():
    """
    One-time task to backfill existing articles/videos into content_items.
    
    Run manually or on startup to initialize curation system.
    """
    logger.info("Starting backfill job")
    
    db = SessionLocal()
    
    try:
        from app.services.ingestion_pipeline import create_ingestion_pipeline
        
        pipeline = create_ingestion_pipeline(db)
        stats = pipeline.run_backfill(hours_back=168, limit=1000)  # 7 days
        
        logger.info(f"Backfill job complete: {stats}")
        
    except Exception as e:
        logger.error(f"Error in backfill job: {str(e)}")
    finally:
        db.close()

def retry_ai_processing():
    """
    Retry AI processing for content that failed previously.
    
    This runs periodically to reprocess content when OpenAI API becomes available.
    Only processes articles and videos, not reels.
    """
    logger.info("Starting AI processing retry job")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.integrations.openai_client import OpenAIClient
        from app.models.content import ContentType
        
        content_repo = ContentItemRepository(db)
        openai_client = OpenAIClient()
        
        # Check if OpenAI is configured
        if not openai_client.is_configured():
            logger.warning("OpenAI API key not configured. Skipping AI retry job.")
            return
        
        # Get content that needs AI processing
        items = content_repo.get_unprocessed_by_ai(limit=50, hours_back=168)  # 7 days
        
        if not items:
            logger.info("No content items need AI processing")
            return
        
        processed = 0
        failed = 0
        
        for item in items:
            try:
                # Skip reels - they don't need summaries
                if item.type == ContentType.REEL:
                    content_repo.mark_ai_processed(item.id, summary="", topics=item.topics or [])
                    processed += 1
                    continue
                
                # Get content for summarization
                text = item.description or item.title
                
                # Generate AI summary
                if item.type == ContentType.ARTICLE:
                    result = openai_client.summarize_article(item.title, text)
                    summary = result.summary
                    topics = result.tags if result.tags else item.topics
                else:  # VIDEO
                    summary = openai_client.summarize_video(item.title, text)
                    topics = item.topics
                
                # Update content item
                if summary and len(summary.strip()) > 50:
                    content_repo.mark_ai_processed(item.id, summary=summary, topics=topics)
                    processed += 1
                    logger.info(f"✓ AI processed: {item.title[:50]}...")
                else:
                    failed += 1
                    logger.warning(f"✗ Empty summary for: {item.title[:50]}...")
                
            except Exception as e:
                failed += 1
                logger.error(f"✗ Failed to process {item.title[:50]}...: {str(e)}")
                continue
        
        logger.info(f"AI retry job complete: {processed} processed, {failed} failed out of {len(items)} total")
        
    except Exception as e:
        logger.error(f"Error in AI retry job: {str(e)}")
    finally:
        db.close()