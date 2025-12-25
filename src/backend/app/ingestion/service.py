"""
Ingestion service.

Orchestrates the ingestion of articles and videos into the content system.
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.article import Article
from app.models.video import Video
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository
from app.repositories.article_repo import ArticleRepository

from app.clustering.dedupe import compute_dedupe_key
from app.clustering.service import ClusteringService
from app.ranking.service import ScoringService
from app.ranking.quality import compute_source_weight
from app.ingestion.extractors import extract_topics, extract_entities, extract_source

logger = get_logger(__name__)


class IngestionPipeline:
    """
    Pipeline for ingesting content into the curation system.
    
    Converts articles and videos into unified content_items,
    extracts metadata, and triggers clustering/scoring.
    """
    
    def __init__(
        self,
        db: Session,
        content_repo: ContentItemRepository,
        article_repo: ArticleRepository,
        clustering_service: ClusteringService,
        scoring_service: ScoringService,
    ):
        self.db = db
        self.content_repo = content_repo
        self.article_repo = article_repo
        self.clustering = clustering_service
        self.scoring = scoring_service
    
    def ingest_article(self, article: Article) -> Optional[ContentItem]:
        """
        Ingest an article into the content_items table.
        
        Args:
            article: Article model instance
            
        Returns:
            Created ContentItem or None if duplicate
        """
        source = extract_source(article.source_url)
        dedupe_key = compute_dedupe_key(article.title, source)
        
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Article already ingested: {article.title}")
            return None
        
        topics = extract_topics(article.title, article.summary or "")
        entities = extract_entities(article.title, article.summary or "")
        
        # Mark as AI processed if article has a proper summary (not empty/generic)
        ai_processed = bool(article.summary and len(article.summary.strip()) > 50)
        
        content_item = ContentItem(
            type=ContentType.ARTICLE,
            source=source,
            source_url=article.source_url,
            published_at=article.published_date or datetime.utcnow(),
            title=article.title,
            description=article.content[:500] if article.content else None,
            summary=article.summary,
            image_url=article.image_url,
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
        
        status = "with AI summary" if ai_processed else "without AI summary (will retry)"
        logger.info(f"Ingested article {status}: {article.title} -> {content_item.id}")
        return content_item
    
    def ingest_video(
        self,
        video: Video,
        content_type: ContentType = ContentType.VIDEO,
    ) -> Optional[ContentItem]:
        """
        Ingest a video into the content_items table.
        
        Args:
            video: Video model instance
            content_type: VIDEO or REEL (determined by duration if not specified)
            
        Returns:
            Created ContentItem or None if duplicate
        """
        if content_type == ContentType.VIDEO and video.duration_seconds:
            if video.duration_seconds < 60:
                content_type = ContentType.REEL
        
        source = video.source or "YouTube"
        dedupe_key = compute_dedupe_key(video.title, source)
        
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Video already ingested: {video.title}")
            return None
        
        text = video.summary or ""
        topics = extract_topics(video.title, text)
        entities = extract_entities(video.title, text)
        
        # Mark as AI processed if video has a proper summary
        # Detect if summary is just a generic YouTube description (not AI generated)
        is_generic = text and ("Watch this video" in text or "Subscribe" in text.lower() or len(text.strip()) < 50)
        ai_processed = bool(text and not is_generic and len(text.strip()) > 50)
        
        # Convert date to datetime for published_at
        published_at = datetime.combine(video.published_date, datetime.min.time()) if video.published_date else datetime.utcnow()
        
        content_item = ContentItem(
            type=content_type,
            source=source,
            source_url=video.video_url,
            published_at=published_at,
            title=video.title,
            description=video.summary[:500] if video.summary else None,
            summary=video.summary if content_type != ContentType.REEL else None,
            image_url=video.thumbnail_url,
            video_url=video.video_url,
            duration_seconds=video.duration_seconds,
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
        
        status = "with AI summary" if ai_processed else "without AI summary (will retry)"
        logger.info(f"Ingested video {status}: {video.title} -> {content_item.id}")
        return content_item
    
    def run_backfill(
        self,
        hours_back: int = 72,
        limit: int = 500,
    ) -> Dict[str, int]:
        """
        Backfill existing articles and videos into content_items.
        
        Args:
            hours_back: How far back to look
            limit: Maximum items per type
            
        Returns:
            Statistics about the backfill
        """
        stats = {
            "articles_processed": 0,
            "articles_ingested": 0,
            "videos_processed": 0,
            "videos_ingested": 0,
            "errors": 0,
        }
        
        # Calculate days from hours for the existing repo method
        days_back = max(1, hours_back // 24)
        
        # Backfill articles - use existing method that filters by date
        articles = self.article_repo.get_articles_last_n_days(days=days_back)[:limit]
        logger.info(f"Backfilling {len(articles)} articles")
        
        for article in articles:
            try:
                stats["articles_processed"] += 1
                if self.ingest_article(article):
                    stats["articles_ingested"] += 1
            except Exception as e:
                logger.error(f"Error ingesting article {article.id}: {e}")
                stats["errors"] += 1
        
        # Backfill videos
        try:
            from app.repositories.video_repo import VideoRepository
            video_repo = VideoRepository(self.db)
            videos = video_repo.get_videos_last_n_days(days=days_back)[:limit]
            
            logger.info(f"Backfilling {len(videos)} videos")
            
            for video in videos:
                try:
                    stats["videos_processed"] += 1
                    if self.ingest_video(video):
                        stats["videos_ingested"] += 1
                except Exception as e:
                    logger.error(f"Error ingesting video {video.id}: {e}")
                    stats["errors"] += 1
        except Exception as e:
            logger.error(f"Error backfilling videos: {e}")
            stats["errors"] += 1
        
        logger.info(f"Backfill complete: {stats}")
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
    from app.repositories.article_repo import ArticleRepository
    from app.repositories.user_repo import InteractionEventRepository
    from app.clustering.service import ClusteringService
    from app.ranking.service import ScoringService
    
    content_repo = ContentItemRepository(db)
    article_repo = ArticleRepository(db)
    event_repo = InteractionEventRepository(db)
    
    clustering = ClusteringService(content_repo)
    scoring = ScoringService(content_repo, event_repo)
    
    return IngestionPipeline(
        db=db,
        content_repo=content_repo,
        article_repo=article_repo,
        clustering_service=clustering,
        scoring_service=scoring,
    )
