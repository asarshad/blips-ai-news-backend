"""
Repository for content items.

Provides data access methods for the unified content_items table.
"""

from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, or_

from app.repositories.base import BaseRepository
from app.models.content import ContentItem, ContentType


class ContentItemRepository(BaseRepository[ContentItem]):
    """Repository for ContentItem CRUD and query operations."""
    
    def __init__(self, db: Session):
        super().__init__(db, ContentItem)
    
    def get_by_source_url(self, source_url: str) -> Optional[ContentItem]:
        """Get content item by source URL."""
        return self.db.query(ContentItem).filter(
            ContentItem.source_url == source_url
        ).first()
    
    def get_by_dedupe_key(self, dedupe_key: str) -> Optional[ContentItem]:
        """Get content item by deduplication key."""
        return self.db.query(ContentItem).filter(
            ContentItem.dedupe_key == dedupe_key
        ).first()
    
    def get_by_type(
        self,
        content_type: ContentType,
        limit: int = 50,
        offset: int = 0,
        hours_back: int = 72
    ) -> List[ContentItem]:
        """
        Get recent content items of a specific type.
        
        Args:
            content_type: ARTICLE, VIDEO, or REEL
            limit: Maximum number of items
            offset: Pagination offset
            hours_back: Only include items from the last N hours
            
        Returns:
            List of content items ordered by global_score
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        return self.db.query(ContentItem).filter(
            ContentItem.type == content_type,
            ContentItem.published_at >= cutoff
        ).order_by(
            desc(ContentItem.global_score),
            desc(ContentItem.published_at)
        ).offset(offset).limit(limit).all()
    
    def get_unscored(self, limit: int = 100) -> List[ContentItem]:
        """Get content items that need scoring (global_score = 0)."""
        return self.db.query(ContentItem).filter(
            ContentItem.global_score == 0.0
        ).limit(limit).all()
    
    def get_unclustered(
        self,
        hours_back: int = 48,
        limit: int = 500
    ) -> List[ContentItem]:
        """Get content items without a cluster_id within the time window."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        return self.db.query(ContentItem).filter(
            ContentItem.cluster_id.is_(None),
            ContentItem.published_at >= cutoff
        ).order_by(
            desc(ContentItem.published_at)
        ).limit(limit).all()
    
    def get_by_cluster(
        self,
        cluster_id: str,
        content_type: Optional[ContentType] = None
    ) -> List[ContentItem]:
        """Get all content items in a cluster, optionally filtered by type."""
        query = self.db.query(ContentItem).filter(
            ContentItem.cluster_id == cluster_id
        )
        
        if content_type:
            query = query.filter(ContentItem.type == content_type)
        
        return query.order_by(desc(ContentItem.global_score)).all()
    
    def get_canonical_for_cluster(
        self,
        cluster_id: str,
        content_type: ContentType
    ) -> Optional[ContentItem]:
        """Get the canonical item for a cluster and type combination."""
        return self.db.query(ContentItem).filter(
            ContentItem.cluster_id == cluster_id,
            ContentItem.type == content_type,
            ContentItem.is_cluster_canonical == 1
        ).first()
    
    def get_recent_with_scores(
        self,
        hours_back: int = 72,
        limit: int = 1000
    ) -> List[ContentItem]:
        """Get recent content items for scoring updates."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        return self.db.query(ContentItem).filter(
            ContentItem.published_at >= cutoff
        ).order_by(
            desc(ContentItem.published_at)
        ).limit(limit).all()
    
    def update_scores(
        self,
        item_id: int,
        quality_score: float,
        trend_score: float,
        recency_score: float,
        diversity_boost: float,
        global_score: float
    ) -> bool:
        """Update all scores for a content item."""
        result = self.db.query(ContentItem).filter(
            ContentItem.id == item_id
        ).update({
            ContentItem.quality_score: quality_score,
            ContentItem.trend_score: trend_score,
            ContentItem.recency_score: recency_score,
            ContentItem.diversity_boost: diversity_boost,
            ContentItem.global_score: global_score,
            ContentItem.updated_at: datetime.utcnow()
        })
        self.db.commit()
        return result > 0
    
    def set_cluster(
        self,
        item_id: int,
        cluster_id: str,
        is_canonical: bool = False
    ) -> bool:
        """Assign a content item to a cluster."""
        result = self.db.query(ContentItem).filter(
            ContentItem.id == item_id
        ).update({
            ContentItem.cluster_id: cluster_id,
            ContentItem.is_cluster_canonical: 1 if is_canonical else 0,
            ContentItem.updated_at: datetime.utcnow()
        })
        self.db.commit()
        return result > 0
    
    def get_items_for_playlist(
        self,
        content_type: ContentType,
        hours_back: int = 72,
        limit: int = 100,
        exclude_cluster_ids: Optional[List[str]] = None
    ) -> List[ContentItem]:
        """
        Get content items for playlist generation.
        
        Returns canonical items only (for clustered content) or all items
        (for unclustered content), excluding specified clusters.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        query = self.db.query(ContentItem).filter(
            ContentItem.type == content_type,
            ContentItem.published_at >= cutoff,
            # Include canonical items OR items without clusters
            or_(
                ContentItem.cluster_id.is_(None),
                ContentItem.is_cluster_canonical == 1
            )
        )
        
        if exclude_cluster_ids:
            query = query.filter(
                ~ContentItem.cluster_id.in_(exclude_cluster_ids)
            )
        
        return query.order_by(
            desc(ContentItem.global_score),
            desc(ContentItem.published_at)
        ).limit(limit).all()
    
    def get_topic_distribution(
        self,
        content_type: ContentType,
        hours_back: int = 24
    ) -> List[Tuple[str, int]]:
        """Get topic distribution for diversity calculation."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        # Query items and aggregate topics
        items = self.db.query(ContentItem).filter(
            ContentItem.type == content_type,
            ContentItem.published_at >= cutoff
        ).all()
        
        topic_counts = {}
        for item in items:
            for topic in (item.topics or []):
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        
        return sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
    
    def bulk_create(self, items_data: List[dict]) -> List[ContentItem]:
        """Create multiple content items efficiently."""
        items = [ContentItem(**data) for data in items_data]
        self.db.bulk_save_objects(items)
        self.db.commit()
        return items
    
    def get_similar_items(
        self,
        item: ContentItem,
        hours_back: int = 48,
        limit: int = 50
    ) -> List[ContentItem]:
        """
        Get potentially similar items for clustering.
        
        Uses entity overlap and time proximity for candidate selection.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        # Get items in the time window (excluding the item itself)
        return self.db.query(ContentItem).filter(
            ContentItem.id != item.id,
            ContentItem.published_at >= cutoff,
            ContentItem.published_at <= item.published_at + timedelta(hours=hours_back),
            ContentItem.published_at >= item.published_at - timedelta(hours=hours_back)
        ).order_by(
            desc(ContentItem.published_at)
        ).limit(limit).all()
    
    # --- Cluster aggregation methods (replaces ContentClusterRepository) ---
    
    def get_cluster_item_count(self, cluster_id: str) -> int:
        """
        Get the number of items in a cluster by aggregating content_items.
        
        This replaces the separate content_clusters table.
        """
        if not cluster_id:
            return 0
        
        count = self.db.query(func.count(ContentItem.id)).filter(
            ContentItem.cluster_id == cluster_id
        ).scalar()
        
        return count or 0
    
    def get_cluster_stats(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """
        Compute cluster statistics from content_items aggregation.
        
        Returns:
            Dictionary with item_count, first_seen, last_seen, or None if cluster doesn't exist
        """
        if not cluster_id:
            return None
        
        stats = self.db.query(
            func.count(ContentItem.id).label('item_count'),
            func.min(ContentItem.published_at).label('first_seen'),
            func.max(ContentItem.published_at).label('last_seen')
        ).filter(
            ContentItem.cluster_id == cluster_id
        ).first()
        
        if not stats or stats.item_count == 0:
            return None
        
        return {
            "item_count": stats.item_count,
            "first_seen": stats.first_seen,
            "last_seen": stats.last_seen
        }
    
    def get_active_cluster_ids(self, hours_back: int = 48) -> List[str]:
        """
        Get unique cluster IDs from recent content.
        
        Returns list of cluster_ids that have content within the time window.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        
        result = self.db.query(
            ContentItem.cluster_id
        ).filter(
            ContentItem.cluster_id.isnot(None),
            ContentItem.published_at >= cutoff
        ).distinct().all()
        
        return [r[0] for r in result if r[0]]
    
    def get_cluster_items(
        self,
        cluster_id: str,
        limit: int = 50
    ) -> List[ContentItem]:
        """Get all items in a cluster."""
        if not cluster_id:
            return []
        
        return self.db.query(ContentItem).filter(
            ContentItem.cluster_id == cluster_id
        ).order_by(
            desc(ContentItem.published_at)
        ).limit(limit).all()

