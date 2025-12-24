"""
Scoring service.

Orchestrates the scoring job that updates content scores periodically.
This is a thin service that coordinates the scoring modules.
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import InteractionEventRepository
from app.models.content import ContentItem, ContentType

from app.ranking.quality import compute_quality_score
from app.ranking.recency import compute_recency_score
from app.ranking.trend import compute_trend_score
from app.ranking.diversity import compute_diversity_boost, compute_topic_distribution
from app.ranking.global_score import compute_global_score

logger = get_logger(__name__)


class ScoringService:
    """
    Service for running scoring updates on content.
    
    Designed to run periodically (e.g., hourly) to keep scores fresh.
    """
    
    def __init__(
        self,
        content_repo: ContentItemRepository,
        interaction_repo: InteractionEventRepository,
    ):
        self.content_repo = content_repo
        self.interaction_repo = interaction_repo
    
    def run_scoring_job(
        self,
        hours_back: int = 72,
        batch_size: int = 2000,
    ) -> Dict[str, int]:
        """
        Run scoring update for recent content.
        
        Args:
            hours_back: How far back to look for content
            batch_size: Maximum items to process per run
            
        Returns:
            Statistics about the scoring run
        """
        stats = {
            "processed": 0,
            "updated": 0,
            "errors": 0,
        }
        
        # Get recent content
        items = self.content_repo.get_recent_with_scores(
            hours_back=hours_back,
            limit=batch_size,
        )
        
        logger.info(f"Running scoring on {len(items)} items")
        
        if not items:
            return stats
        
        # Pre-compute topic distribution for diversity
        topic_distributions = self._compute_topic_distributions(hours_back)
        
        # Score each item
        for item in items:
            try:
                stats["processed"] += 1
                
                scores = self._compute_item_scores(item, topic_distributions)
                
                success = self.content_repo.update_scores(
                    item.id,
                    quality_score=scores["quality"],
                    trend_score=scores["trend"],
                    recency_score=scores["recency"],
                    global_score=scores["global"],
                )
                
                if success:
                    stats["updated"] += 1
                    
            except Exception as e:
                logger.error(f"Error scoring item {item.id}: {e}")
                stats["errors"] += 1
        
        logger.info(f"Scoring complete: {stats}")
        return stats
    
    def score_single_item(self, item: ContentItem) -> Dict[str, float]:
        """
        Compute scores for a single item.
        
        Useful for scoring new content immediately after ingestion.
        
        Args:
            item: Content item to score
            
        Returns:
            Dictionary of scores
        """
        topic_distributions = self._compute_topic_distributions(hours_back=72)
        return self._compute_item_scores(item, topic_distributions)
    
    def _compute_item_scores(
        self,
        item: ContentItem,
        topic_distributions: Dict[ContentType, Dict[str, float]],
    ) -> Dict[str, float]:
        """
        Compute all scores for a content item.
        
        Args:
            item: Content item to score
            topic_distributions: Pre-computed topic distributions
            
        Returns:
            Dictionary with quality, trend, recency, diversity, global scores
        """
        # Quality score
        quality = compute_quality_score(
            source=item.source or "",
            title=item.title,
            summary=item.summary,
            description=item.description,
            image_url=item.image_url,
            topics=item.topics,
            entities=item.entities,
        )
        
        # Recency score
        recency = compute_recency_score(
            published_at=item.published_at or datetime.now(timezone.utc),
        )
        
        # Trend score (cluster size + engagement)
        engagement_events = self._get_engagement_events(item.id)
        cluster_size = self._get_cluster_size(item.cluster_id)
        
        trend = compute_trend_score(
            cluster_size=cluster_size,
            engagement_events=engagement_events,
        )
        
        # Diversity boost
        topic_dist = topic_distributions.get(item.type, {})
        diversity = compute_diversity_boost(
            item_topics=item.topics or [],
            topic_distribution=topic_dist,
        )
        
        # Global score
        global_score = compute_global_score(
            quality_score=quality,
            trend_score=trend,
            recency_score=recency,
            diversity_boost=diversity,
        )
        
        return {
            "quality": quality,
            "trend": trend,
            "recency": recency,
            "diversity": diversity,
            "global": global_score,
        }
    
    def _compute_topic_distributions(
        self,
        hours_back: int,
    ) -> Dict[ContentType, Dict[str, float]]:
        """
        Compute topic distribution for each content type.
        
        Args:
            hours_back: Time window for distribution
            
        Returns:
            Mapping of content type -> topic distribution
        """
        distributions = {}
        
        for content_type in ContentType:
            items = self.content_repo.get_by_type(
                content_type=content_type,
                hours_back=hours_back,
                limit=500,
            )
            
            # Convert to dict format for compute_topic_distribution
            item_dicts = [
                {"topics": item.topics or []}
                for item in items
            ]
            
            distributions[content_type] = compute_topic_distribution(item_dicts)
        
        return distributions
    
    def _get_engagement_events(self, content_id: int) -> List[Dict]:
        """Get recent engagement events for content."""
        try:
            events = self.interaction_repo.get_events_for_content(
                content_id=content_id,
                hours_back=24,
            )
            return [{"type": e.event_type, "weight": 1} for e in events]
        except Exception:
            return []
    
    def _get_cluster_size(self, cluster_id: Optional[str]) -> int:
        """Get size of content cluster."""
        if not cluster_id:
            return 1
        
        try:
            return self.content_repo.get_cluster_size(cluster_id)
        except Exception:
            return 1
