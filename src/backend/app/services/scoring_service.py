"""
Scoring service for content ranking.

Implements the global scoring algorithm:
- quality_score: Source weight + completeness
- trend_score: Cluster size + engagement signals
- recency_score: Exponential decay from publish time
- diversity_boost: Penalize topic dominance

global_score = 0.40 * quality + 0.30 * trend + 0.20 * recency + 0.10 * diversity
"""

import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter

from app.core.logging import get_logger
from app.core.config import get_settings
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import InteractionEventRepository
from app.models.content import ContentItem, ContentType, EventType

logger = get_logger(__name__)
settings = get_settings()


# Scoring configuration
SCORE_WEIGHTS = {
    "quality": 0.40,
    "trend": 0.30,
    "recency": 0.20,
    "diversity": 0.10
}

# Source quality weights (0-1 scale)
SOURCE_QUALITY_WEIGHTS = {
    # Premium tech sources
    "techcrunch": 0.9,
    "the verge": 0.9,
    "wired": 0.85,
    "ars technica": 0.9,
    "mit technology review": 0.95,
    "ieee spectrum": 0.9,
    
    # Major tech outlets
    "engadget": 0.75,
    "mashable": 0.7,
    "cnet": 0.75,
    "zdnet": 0.75,
    "venturebeat": 0.8,
    
    # YouTube channels (quality varies)
    "mkbhd": 0.9,
    "linus tech tips": 0.8,
    "dave2d": 0.85,
    "austin evans": 0.75,
    "unbox therapy": 0.7,
    
    # Company blogs (high authority)
    "google": 0.95,
    "microsoft": 0.95,
    "apple": 0.95,
    "meta": 0.9,
    "openai": 0.95,
    "anthropic": 0.95,
    
    # Default
    "default": 0.5
}

# Recency decay parameters
RECENCY_HALF_LIFE_HOURS = 24  # Score halves every 24 hours
MAX_RECENCY_AGE_HOURS = 168  # 7 days

# Engagement weights for trend score
ENGAGEMENT_WEIGHTS = {
    EventType.VIEW_10S: 1,
    EventType.OPEN_SOURCE: 3,
    EventType.SHARE: 5,
    EventType.SAVE: 5,
    EventType.CHAT_START: 6,
    EventType.CHAT_MESSAGE: 1,
}

# Diversity parameters
MAX_TOPIC_DOMINANCE = 0.40  # 40% max for any single topic


class ScoringService:
    """
    Service for computing content scores.
    
    Implements hourly scoring updates for all recent content,
    computing quality, trend, recency, and diversity scores.
    """
    
    def __init__(
        self,
        content_repo: ContentItemRepository,
        interaction_repo: InteractionEventRepository
    ):
        self.content_repo = content_repo
        self.interaction_repo = interaction_repo
    
    def run_scoring_job(self, hours_back: int = 72) -> Dict[str, int]:
        """
        Run scoring update for all recent content.
        
        Args:
            hours_back: How far back to look for content
            
        Returns:
            Statistics about the scoring run
        """
        stats = {
            "processed": 0,
            "updated": 0,
            "errors": 0
        }
        
        # Get recent content
        items = self.content_repo.get_recent_with_scores(
            hours_back=hours_back,
            limit=2000
        )
        
        logger.info(f"Running scoring on {len(items)} items")
        
        # Compute topic distribution for diversity
        topic_distributions = {}
        for content_type in ContentType:
            topic_distributions[content_type] = self._compute_topic_distribution(
                content_type, hours_back
            )
        
        # Score each item
        for item in items:
            try:
                stats["processed"] += 1
                
                scores = self._compute_all_scores(
                    item,
                    topic_distributions.get(item.type, {})
                )
                
                # Update scores in database
                success = self.content_repo.update_scores(
                    item.id,
                    quality_score=scores["quality"],
                    trend_score=scores["trend"],
                    recency_score=scores["recency"],
                    diversity_boost=scores["diversity"],
                    global_score=scores["global"]
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
        Compute scores for a single item (for new content).
        
        Returns:
            Dictionary with all score components
        """
        topic_distribution = self._compute_topic_distribution(item.type)
        return self._compute_all_scores(item, topic_distribution)
    
    def _compute_all_scores(
        self,
        item: ContentItem,
        topic_distribution: Dict[str, float]
    ) -> Dict[str, float]:
        """Compute all score components for an item."""
        quality = self._compute_quality_score(item)
        trend = self._compute_trend_score(item)
        recency = self._compute_recency_score(item)
        diversity = self._compute_diversity_boost(item, topic_distribution)
        
        # Compute weighted global score
        global_score = (
            SCORE_WEIGHTS["quality"] * quality +
            SCORE_WEIGHTS["trend"] * trend +
            SCORE_WEIGHTS["recency"] * recency +
            SCORE_WEIGHTS["diversity"] * diversity
        )
        
        return {
            "quality": quality,
            "trend": trend,
            "recency": recency,
            "diversity": diversity,
            "global": global_score
        }
    
    def _compute_quality_score(self, item: ContentItem) -> float:
        """
        Compute quality score based on source and content completeness.
        
        Components:
        - Source weight (50%)
        - Content completeness (30%)
        - Metadata richness (20%)
        """
        # Source weight
        source_key = item.source.lower().strip()
        source_weight = SOURCE_QUALITY_WEIGHTS.get(
            source_key,
            SOURCE_QUALITY_WEIGHTS["default"]
        )
        
        # Content completeness
        completeness = 0.0
        if item.title:
            completeness += 0.3
        if item.description:
            completeness += 0.2
        if item.summary and item.type != ContentType.REEL:
            completeness += 0.3
        if item.image_url:
            completeness += 0.1
        if item.video_url and item.type in (ContentType.VIDEO, ContentType.REEL):
            completeness += 0.1
        
        # Metadata richness
        metadata = 0.0
        if item.topics and len(item.topics) > 0:
            metadata += 0.5
        if item.entities and len(item.entities) > 0:
            metadata += 0.5
        
        # Weighted combination
        quality = (
            0.50 * source_weight +
            0.30 * completeness +
            0.20 * metadata
        )
        
        return min(1.0, max(0.0, quality))
    
    def _compute_trend_score(self, item: ContentItem) -> float:
        """
        Compute trend score based on cluster size and engagement.
        
        Components:
        - Cluster size (80%): More coverage = bigger story
        - Engagement signals (20%): User interactions (capped for early-stage)
        
        NOTE: Engagement is capped at 20% to avoid gaming in early-stage systems
        before we have robust engagement data.
        """
        # Cluster size component
        cluster_score = 0.0
        if item.cluster_id:
            item_count = self.content_repo.get_cluster_item_count(item.cluster_id)
            if item_count > 0:
                # Logarithmic scaling: 1 item = 0, 2 items = 0.5, 5+ items = 1.0
                cluster_score = min(1.0, math.log2(item_count + 1) / 3)
        
        # Engagement component
        engagement_score = 0.0
        try:
            event_counts = self.interaction_repo.count_events_by_type(
                item.id, hours_back=48
            )
            
            weighted_engagement = sum(
                count * ENGAGEMENT_WEIGHTS.get(event_type, 1)
                for event_type, count in event_counts.items()
            )
            
            # Logarithmic scaling: 0 = 0, 10 = 0.5, 100+ = 1.0
            engagement_score = min(1.0, math.log10(weighted_engagement + 1) / 2)
            
        except Exception as e:
            logger.debug(f"Error computing engagement for item {item.id}: {e}")
        
        # Weighted combination using configurable weights
        # Engagement is capped (default 20%) for early-stage systems to avoid gaming
        trend = (
            settings.TREND_CLUSTER_WEIGHT * cluster_score + 
            settings.TREND_ENGAGEMENT_WEIGHT * engagement_score
        )
        
        return min(1.0, max(0.0, trend))
    
    def _compute_recency_score(self, item: ContentItem) -> float:
        """
        Compute recency score using exponential decay.
        
        Score = 2^(-age_hours / half_life)
        
        Examples (with 24h half-life):
        - 0 hours old: 1.0
        - 24 hours old: 0.5
        - 48 hours old: 0.25
        - 72 hours old: 0.125
        """
        now = datetime.utcnow()
        age_hours = (now - item.published_at).total_seconds() / 3600
        
        # Cap at max age
        if age_hours > MAX_RECENCY_AGE_HOURS:
            return 0.0
        
        # Exponential decay
        recency = math.pow(2, -age_hours / RECENCY_HALF_LIFE_HOURS)
        
        return min(1.0, max(0.0, recency))
    
    def _compute_diversity_boost(
        self,
        item: ContentItem,
        topic_distribution: Dict[str, float]
    ) -> float:
        """
        Compute diversity boost to penalize topic dominance.
        
        Items in underrepresented topics get a boost,
        items in dominant topics get penalized.
        """
        if not item.topics or not topic_distribution:
            return 0.0
        
        # Get the representation of this item's topics
        item_topic_shares = []
        for topic in item.topics:
            share = topic_distribution.get(topic.lower(), 0.0)
            item_topic_shares.append(share)
        
        if not item_topic_shares:
            return 0.0
        
        # Average topic share for this item
        avg_share = sum(item_topic_shares) / len(item_topic_shares)
        
        # If topics are overrepresented (> 40% of feed), penalize
        # If topics are underrepresented (< 10% of feed), boost
        if avg_share > MAX_TOPIC_DOMINANCE:
            # Penalty: -0.5 max
            boost = -0.5 * ((avg_share - MAX_TOPIC_DOMINANCE) / (1 - MAX_TOPIC_DOMINANCE))
        elif avg_share < 0.10:
            # Boost: +0.5 max
            boost = 0.5 * ((0.10 - avg_share) / 0.10)
        else:
            # Neutral zone
            boost = 0.0
        
        return min(0.5, max(-0.5, boost))
    
    def _compute_topic_distribution(
        self,
        content_type: ContentType,
        hours_back: int = 24
    ) -> Dict[str, float]:
        """
        Compute topic distribution for a content type.
        
        Returns:
            Dictionary mapping topic -> share (0-1)
        """
        topic_counts = self.content_repo.get_topic_distribution(
            content_type, hours_back
        )
        
        if not topic_counts:
            return {}
        
        total = sum(count for _, count in topic_counts)
        
        return {
            topic.lower(): count / total
            for topic, count in topic_counts
        }
    
    def get_scoring_stats(self) -> Dict[str, any]:
        """Get statistics about scoring."""
        stats = {"by_type": {}}
        
        for content_type in ContentType:
            items = self.content_repo.get_by_type(
                content_type, limit=100, hours_back=24
            )
            
            if not items:
                continue
            
            scores = [item.global_score for item in items]
            stats["by_type"][content_type.value] = {
                "count": len(items),
                "avg_score": sum(scores) / len(scores) if scores else 0,
                "max_score": max(scores) if scores else 0,
                "min_score": min(scores) if scores else 0,
            }
        
        return stats


def get_source_quality_weight(source: str) -> float:
    """Get quality weight for a source."""
    source_key = source.lower().strip()
    return SOURCE_QUALITY_WEIGHTS.get(
        source_key,
        SOURCE_QUALITY_WEIGHTS["default"]
    )
