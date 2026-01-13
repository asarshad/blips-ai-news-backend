"""
Scheduled tasks for the Blips worker service.

All background jobs run here - NEVER in the web API process.
Jobs are designed to be:
- Idempotent: safe to run multiple times
- Rate-limited: respect external API limits  
- LLM-capped: limit AI calls per run
- Logged: comprehensive start/end/count/error logging
- Feature-gated: respect feature flags
"""

import time

from app.db.base import SessionLocal
from app.core.dependencies import get_redis
from app.core.logging import get_logger
from app.core.feature_flags import feature_flags
from app.scheduler.config import MAX_LLM_CALLS_PER_RUN, MAX_ITEMS_PER_RUN, LLM_RATE_LIMIT_DELAY
from app.scheduler.job_stats import JobStats, log_job_start

logger = get_logger(__name__)


# =============================================================================
# News Fetching Task
# =============================================================================

def fetch_and_process_news():
    """
    Fetch, summarize, and store new articles.
    
    Feature flags: ingestion, summarization, videos
    Idempotency: Uses source_url as dedupe key
    Rate limiting: Delays between LLM calls
    LLM cap: MAX_LLM_CALLS_PER_RUN per run
    """
    # Check ingestion feature flag
    if not feature_flags.is_enabled("ingestion"):
        logger.info("[fetch_news] SKIPPED - ingestion feature is disabled")
        return
    
    stats = log_job_start("fetch_news")
    
    db = SessionLocal()
    
    try:
        # Run curation ingestion (TODO: implement direct RSS/YouTube fetching)
        _run_curation_ingestion_with_stats(db, stats)
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[fetch_news] Fatal error: {str(e)}")
        db.rollback()
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def _run_curation_ingestion_with_stats(db, stats: JobStats):
    """Run curation ingestion with error tracking."""
    try:
        from app.services.ingestion_pipeline import create_ingestion_pipeline
        
        pipeline = create_ingestion_pipeline(db)
        result = pipeline.run_backfill(hours_back=24, limit=500)
        
        logger.info(f"[fetch_news] Curation ingestion: {result}")
        
    except Exception as e:
        stats.errors.append(f"Curation ingestion: {str(e)}")


# ============================================================================
# Curation System Tasks
# ============================================================================

def run_scoring_job():
    """
    Update content scores.
    
    Idempotency: Recalculates scores from current state
    No LLM calls: Pure computation
    """
    stats = log_job_start("scoring")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.repositories.user_repo import InteractionEventRepository
        from app.services.scoring_service import ScoringService
        
        content_repo = ContentItemRepository(db)
        event_repo = InteractionEventRepository(db)
        
        scoring = ScoringService(content_repo, event_repo)
        result = scoring.run_scoring_job(hours_back=72)
        
        stats.items_processed = result.get('items_scored', 0) if isinstance(result, dict) else 0
        logger.info(f"[scoring] Result: {result}")
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[scoring] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def run_clustering_job():
    """
    Cluster unclustered content.
    
    Feature flag: clustering
    Idempotency: Processes only unclustered items
    No LLM calls: Uses embedding similarity
    """
    # Check clustering feature flag
    if not feature_flags.is_enabled("clustering"):
        logger.info("[clustering] SKIPPED - clustering feature is disabled")
        return
    
    stats = log_job_start("clustering")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.services.clustering_service import ClusteringService
        
        content_repo = ContentItemRepository(db)
        
        clustering = ClusteringService(content_repo)
        result = clustering.run_clustering_job()
        
        stats.items_processed = result.get('items_clustered', 0) if isinstance(result, dict) else 0
        logger.info(f"[clustering] Result: {result}")
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[clustering] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def run_preference_decay_job():
    """
    Decay user preferences over time.
    
    Feature flag: personalization
    Idempotency: Applies decay factor to current values
    No LLM calls: Pure computation
    """
    # Check personalization feature flag
    if not feature_flags.is_enabled("personalization"):
        logger.info("[preference_decay] SKIPPED - personalization feature is disabled")
        return
    
    stats = log_job_start("preference_decay")
    
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
        
        result = personalization.run_decay_job()
        stats.items_processed = result.get('profiles_updated', 0) if isinstance(result, dict) else 0
        logger.info(f"[preference_decay] Result: {result}")
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[preference_decay] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def run_backfill_job():
    """
    Backfill existing articles/videos into content_items.
    
    Idempotency: Skips items already in content_items
    Run manually or on startup to initialize curation system.
    """
    stats = log_job_start("backfill")
    
    db = SessionLocal()
    
    try:
        from app.services.ingestion_pipeline import create_ingestion_pipeline
        
        pipeline = create_ingestion_pipeline(db)
        result = pipeline.run_backfill(hours_back=168, limit=1000)  # 7 days
        
        stats.items_processed = result.get('items_created', 0) if isinstance(result, dict) else 0
        logger.info(f"[backfill] Result: {result}")
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[backfill] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def retry_ai_processing():
    """
    Retry AI processing for content that failed previously.
    
    Feature flag: summarization
    Idempotency: Only processes items marked as unprocessed
    Rate limiting: Delays between LLM calls
    LLM cap: MAX_LLM_CALLS_PER_RUN per run
    """
    # Check summarization feature flag
    if not feature_flags.is_enabled("summarization"):
        logger.info("[ai_retry] SKIPPED - summarization feature is disabled")
        return
    
    stats = log_job_start("ai_retry")
    
    db = SessionLocal()
    
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.integrations.llm_client import LLMClient
        from app.models.content import ContentType
        
        content_repo = ContentItemRepository(db)
        llm_client = LLMClient()
        
        # Check if LLM is configured
        if not llm_client.is_configured():
            logger.warning(f"[ai_retry] {llm_client.get_provider()} API key not configured, skipping")
            return
        
        # Get content that needs AI processing
        items = content_repo.get_unprocessed_by_ai(limit=MAX_ITEMS_PER_RUN, hours_back=168)
        
        if not items:
            logger.info("[ai_retry] No items need processing")
            return
        
        logger.info(f"[ai_retry] Found {len(items)} items to process")
        
        for item in items:
            # Check LLM cap
            if stats.llm_calls >= MAX_LLM_CALLS_PER_RUN:
                logger.warning(f"[ai_retry] LLM cap reached ({MAX_LLM_CALLS_PER_RUN}), stopping")
                stats.items_skipped = len(items) - stats.items_processed - stats.items_failed
                break
            
            try:
                # Skip reels - they don't need summaries
                if item.type == ContentType.REEL:
                    content_repo.mark_ai_processed(item.id, summary="", topics=item.topics or [])
                    stats.items_processed += 1
                    continue
                
                # Get content for summarization
                text = item.description or item.title
                
                # Generate AI summary
                if item.type == ContentType.ARTICLE:
                    result = llm_client.summarize_article(item.title, text)
                    summary = result.summary
                    topics = result.tags if result.tags else item.topics
                else:  # VIDEO
                    summary = llm_client.summarize_video(item.title, text)
                    topics = item.topics
                
                stats.llm_calls += 1
                
                # Update content item
                if summary and len(summary.strip()) > 50:
                    content_repo.mark_ai_processed(item.id, summary=summary, topics=topics)
                    stats.items_processed += 1
                else:
                    stats.items_failed += 1
                    stats.errors.append(f"Empty summary: {item.title[:50]}")
                
                # Rate limit delay
                time.sleep(LLM_RATE_LIMIT_DELAY)
                
            except Exception as e:
                stats.items_failed += 1
                stats.errors.append(f"{item.title[:50]}: {str(e)}")
                continue
        
    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[ai_retry] Fatal error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()