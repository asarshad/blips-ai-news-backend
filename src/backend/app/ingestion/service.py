"""
Ingestion service.

Orchestrates the ingestion of articles and videos into the content system.
Fetches directly from RSS feeds and YouTube channels.
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository

from app.clustering.dedupe import compute_dedupe_key
from app.clustering.service import ClusteringService
from app.ranking.service import ScoringService
from app.ranking.quality import compute_source_weight
from app.ingestion.extractors import extract_topics, extract_entities, extract_source

from app.integrations.rss_client import RSSClient, FeedEntry
from app.integrations.youtube_client import YouTubeClient, VideoEntry
from app.integrations.llm_client import LLMClient

logger = get_logger(__name__)

# Content limits per type
ARTICLE_LIMIT = 30
VIDEO_LIMIT = 30
ENTRIES_PER_FEED = 10
VIDEOS_PER_CHANNEL = 5


class IngestionPipeline:
    """
    Pipeline for ingesting content into the curation system.
    
    Fetches directly from RSS feeds and YouTube channels,
    extracts metadata, and triggers clustering/scoring.
    """
    
    def __init__(
        self,
        db: Session,
        content_repo: ContentItemRepository,
        clustering_service: ClusteringService,
        scoring_service: ScoringService,
        rss_client: Optional[RSSClient] = None,
        youtube_client: Optional[YouTubeClient] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.db = db
        self.content_repo = content_repo
        self.clustering = clustering_service
        self.scoring = scoring_service
        self.rss_client = rss_client or RSSClient()
        self.youtube_client = youtube_client or YouTubeClient()
        self.llm_client = llm_client or LLMClient()
    
    def ingest_rss_entry(self, entry: FeedEntry) -> Optional[ContentItem]:
        """
        Ingest an RSS feed entry directly into content_items.
        
        Args:
            entry: FeedEntry from RSS client
            
        Returns:
            Created ContentItem or None if duplicate
        """
        source = extract_source(entry.url)
        dedupe_key = compute_dedupe_key(entry.title, source)
        
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Article already ingested: {entry.title}")
            return None
        
        # Generate AI summary
        summary = None
        ai_processed = False
        try:
            if self.llm_client.is_configured() and entry.content:
                result = self.llm_client.summarize_article(entry.title, entry.content)
                summary = result.summary
                ai_processed = bool(summary and len(summary.strip()) > 50)
        except Exception as e:
            logger.warning(f"Failed to summarize article {entry.title}: {e}")
        
        topics = extract_topics(entry.title, summary or entry.content[:500] if entry.content else "")
        entities = extract_entities(entry.title, summary or "")
        
        content_item = ContentItem(
            type=ContentType.ARTICLE,
            source=source,
            source_url=entry.url,
            published_at=entry.published_date or datetime.utcnow(),
            title=entry.title,
            description=entry.content[:500] if entry.content else None,
            summary=summary,
            image_url=entry.image_url,
            video_url=None,
            duration_seconds=None,
            topics=topics,
            entities=entities,
            dedupe_key=dedupe_key,
            ai_processed=ai_processed,
        )
        
        content_item.quality_score = compute_source_weight(source)
        content_item.recency_score = 1.0
        
        self.db.add(content_item)
        self.db.commit()
        self.db.refresh(content_item)
        
        self.clustering.cluster_new_item(content_item)
        self._update_scores(content_item)
        
        status = "with AI summary" if ai_processed else "without AI summary"
        logger.info(f"Ingested article {status}: {entry.title} -> {content_item.id}")
        return content_item
    
    def ingest_youtube_entry(
        self,
        entry: VideoEntry,
        content_type: ContentType = ContentType.VIDEO,
    ) -> Optional[ContentItem]:
        """
        Ingest a YouTube video entry directly into content_items.
        
        Args:
            entry: VideoEntry from YouTube client
            content_type: VIDEO or REEL (determined by duration if not specified)
            
        Returns:
            Created ContentItem or None if duplicate
        """
        # Get video duration and check if it's a Short
        duration_seconds = None
        try:
            duration_seconds = self.youtube_client.get_video_duration(entry.video_id)
        except Exception as e:
            logger.warning(f"Failed to get duration for video {entry.title}: {e}")
        
        # Classify as REEL if it's a Short
        if content_type == ContentType.VIDEO:
            is_shorts_url = entry.video_url and "/shorts/" in entry.video_url
            is_short_duration = duration_seconds and duration_seconds <= 180
            if is_shorts_url or is_short_duration:
                content_type = ContentType.REEL
        
        source = entry.source or "YouTube"
        dedupe_key = compute_dedupe_key(entry.title, source)
        
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Video already ingested: {entry.title}")
            return None
        
        # Generate AI summary
        summary = entry.summary
        ai_processed = False
        try:
            if self.llm_client.is_configured() and summary:
                # Check if summary is generic
                is_generic = "Watch this video" in summary or "Subscribe" in summary.lower() or len(summary.strip()) < 50
                if is_generic:
                    # Try to get transcript
                    transcript = self.youtube_client.get_transcript(entry.video_id)
                    if transcript:
                        summary = transcript[:5000]
                
                ai_summary = self.llm_client.summarize_video(entry.title, summary)
                if ai_summary and len(ai_summary.strip()) > 50:
                    summary = ai_summary
                    ai_processed = True
        except Exception as e:
            logger.warning(f"Failed to summarize video {entry.title}: {e}")
        
        topics = extract_topics(entry.title, summary or "")
        entities = extract_entities(entry.title, summary or "")
        
        content_item = ContentItem(
            type=content_type,
            source=source,
            source_url=entry.video_url,
            published_at=datetime.utcnow(),  # YouTube RSS doesn't provide reliable dates
            title=entry.title,
            description=summary[:500] if summary else None,
            summary=summary if content_type != ContentType.REEL else None,
            image_url=entry.thumbnail_url,
            video_url=entry.video_url,
            duration_seconds=duration_seconds,
            topics=topics,
            entities=entities,
            dedupe_key=dedupe_key,
            ai_processed=ai_processed,
        )
        
        content_item.quality_score = compute_source_weight(source)
        content_item.recency_score = 1.0
        
        self.db.add(content_item)
        self.db.commit()
        self.db.refresh(content_item)
        
        self.clustering.cluster_new_item(content_item)
        self._update_scores(content_item)
        
        status = "with AI summary" if ai_processed else "without AI summary"
        logger.info(f"Ingested {content_type.value} {status}: {entry.title} -> {content_item.id}")
        return content_item
    
    def run_backfill(
        self,
        hours_back: int = 72,
        limit: int = 500,
    ) -> Dict[str, int]:
        """
        Fetch and ingest content from RSS feeds and YouTube channels in parallel.
        
        Continues processing until we have ARTICLE_LIMIT new articles and
        VIDEO_LIMIT new videos (skipping duplicates).
        
        Args:
            hours_back: Not used (kept for API compatibility)
            limit: Not used (uses per-type limits instead)
            
        Returns:
            Statistics about the ingestion
        """
        stats = {
            "articles_processed": 0,
            "articles_ingested": 0,
            "articles_skipped_duplicates": 0,
            "videos_processed": 0,
            "videos_ingested": 0,
            "videos_skipped_duplicates": 0,
            "errors": 0,
        }
        
        # Fetch articles and videos in parallel
        logger.info("Fetching content from RSS feeds and YouTube channels in parallel...")
        
        feed_entries: List[FeedEntry] = []
        video_entries: List[VideoEntry] = []
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both fetch tasks
            article_future = executor.submit(
                self.rss_client.fetch_all_feeds, 
                entries_per_feed=ENTRIES_PER_FEED
            )
            video_future = executor.submit(
                self.youtube_client.fetch_all_channels, 
                videos_per_channel=VIDEOS_PER_CHANNEL
            )
            
            # Collect results
            try:
                feed_entries = article_future.result(timeout=120)
                logger.info(f"Fetched {len(feed_entries)} RSS entries")
            except Exception as e:
                logger.error(f"Error fetching RSS feeds: {e}")
                stats["errors"] += 1
            
            try:
                video_entries = video_future.result(timeout=120)
                logger.info(f"Fetched {len(video_entries)} YouTube videos")
            except Exception as e:
                logger.error(f"Error fetching YouTube channels: {e}")
                stats["errors"] += 1
        
        # Process articles until we have ARTICLE_LIMIT new ones (or run out of entries)
        logger.info(f"Processing articles until we have {ARTICLE_LIMIT} new ones...")
        for entry in feed_entries:
            if stats["articles_ingested"] >= ARTICLE_LIMIT:
                break
            stats["articles_processed"] += 1
            try:
                result = self.ingest_rss_entry(entry)
                if result:
                    stats["articles_ingested"] += 1
                else:
                    stats["articles_skipped_duplicates"] += 1
            except Exception as e:
                logger.error(f"Error ingesting article {entry.title}: {e}")
                stats["errors"] += 1
        
        # Process videos until we have VIDEO_LIMIT new ones (or run out of entries)
        logger.info(f"Processing videos until we have {VIDEO_LIMIT} new ones...")
        for entry in video_entries:
            if stats["videos_ingested"] >= VIDEO_LIMIT:
                break
            stats["videos_processed"] += 1
            try:
                result = self.ingest_youtube_entry(entry)
                if result:
                    stats["videos_ingested"] += 1
                else:
                    stats["videos_skipped_duplicates"] += 1
            except Exception as e:
                logger.error(f"Error ingesting video {entry.title}: {e}")
                stats["errors"] += 1
        
        logger.info(f"Ingestion complete: {stats}")
        return stats
    
    def _update_scores(self, content_item: ContentItem) -> None:
        """Update scores for a newly ingested item."""
        scores = self.scoring.score_single_item(content_item)
        self.content_repo.update_scores(
            content_item.id,
            quality_score=scores["quality"],
            trend_score=scores["trend"],
            recency_score=scores["recency"],
            diversity_boost=scores["diversity"],
            global_score=scores["global"],
        )


def create_ingestion_pipeline(db: Session) -> IngestionPipeline:
    """Factory function to create IngestionPipeline with dependencies."""
    from app.repositories.content_repo import ContentItemRepository
    from app.repositories.user_repo import InteractionEventRepository
    from app.clustering.service import ClusteringService
    from app.ranking.service import ScoringService
    
    content_repo = ContentItemRepository(db)
    event_repo = InteractionEventRepository(db)
    
    clustering = ClusteringService(content_repo)
    scoring = ScoringService(content_repo, event_repo)
    
    return IngestionPipeline(
        db=db,
        content_repo=content_repo,
        clustering_service=clustering,
        scoring_service=scoring,
    )
