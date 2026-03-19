"""
Repository for content items.

Provides data access methods for the unified content_items table.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType
from app.repositories.base import BaseRepository
from app.video_surface_rules import surface_content_filter, visible_promotion_filter

# Items with language=NULL are legacy rows inserted before language detection
# was added. Treat them as English to avoid breaking the feed for existing data.
_ENGLISH_FILTER = or_(ContentItem.language == "en", ContentItem.language.is_(None))


class ContentItemRepository(BaseRepository[ContentItem]):
    """Repository for ContentItem CRUD and query operations."""

    def __init__(self, db: Session):
        super().__init__(db, ContentItem)

    def get_by_source_url(self, source_url: str) -> Optional[ContentItem]:
        """Get content item by source URL."""
        return self.db.query(ContentItem).filter(ContentItem.source_url == source_url).first()

    def get_by_dedupe_key(self, dedupe_key: str) -> Optional[ContentItem]:
        """Get content item by deduplication key."""
        return self.db.query(ContentItem).filter(ContentItem.dedupe_key == dedupe_key).first()

    def get_by_canonical_url(self, canonical_url: str) -> Optional[ContentItem]:
        """Get content item by canonical URL."""
        return self.db.query(ContentItem).filter(ContentItem.canonical_url == canonical_url).first()

    def get_by_canonical_key(self, canonical_key: str) -> Optional[ContentItem]:
        """Get content item by canonical key (sha256 hash of normalized URL)."""
        return self.db.query(ContentItem).filter(ContentItem.canonical_key == canonical_key).first()

    def count_created_on_date(
        self,
        content_type: ContentType,
        day_utc: date,
    ) -> int:
        """Count items of a type created within the given UTC date."""
        start = datetime.combine(day_utc, datetime.min.time())
        end = start + timedelta(days=1)
        return int(
            self.db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == content_type,
                ContentItem.created_at >= start,
                ContentItem.created_at < end,
            )
            .scalar()
            or 0
        )

    def get_by_type(
        self,
        content_type: ContentType,
        limit: int = 50,
        offset: int = 0,
        hours_back: int = 72,
        ai_processed_only: bool = True,
    ) -> List[ContentItem]:
        """
        Get recent content items of a specific type.

        Only PROMOTED items are returned – CANDIDATE items are hidden from
        the default feed until they pass the quality gate.

        Args:
            content_type: ARTICLE, VIDEO, or REEL
            limit: Maximum number of items
            offset: Pagination offset
            hours_back: Only include items from the last N hours
            ai_processed_only: Only return AI-processed content (default True)

        Returns:
            List of content items ordered by global_score
        """
        from app.models.content import ContentStatus

        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        query = self.db.query(ContentItem).filter(
            ContentItem.type == content_type,
            ContentItem.published_at >= cutoff,
            ContentItem.is_suppressed.is_(False),
            ContentItem.curation_status == ContentStatus.PROMOTED,
            _ENGLISH_FILTER,
        )

        if ai_processed_only:
            query = query.filter(ContentItem.ai_processed.is_(True))

        return (
            query.order_by(desc(ContentItem.global_score), desc(ContentItem.published_at))
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_unscored(self, limit: int = 100) -> List[ContentItem]:
        """Get content items that need scoring (global_score = 0)."""
        return self.db.query(ContentItem).filter(ContentItem.global_score == 0.0).limit(limit).all()

    def get_unprocessed_by_ai(self, limit: int = 100, hours_back: int = 168) -> List[ContentItem]:
        """
        Get content items that need AI processing.

        Only returns PROMOTED items – CANDIDATE items are intentionally skipped
        to avoid spending LLM tokens on content that may never reach the feed.

        Args:
            limit: Maximum number of items to return
            hours_back: Only include items from the last N hours (default 7 days)

        Returns:
            List of content items without AI processing, PROMOTED status only
        """
        from app.models.content import ContentStatus

        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        return (
            self.db.query(ContentItem)
            .filter(
                ContentItem.ai_processed.is_(False),
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )

    def mark_ai_processed(self, item_id: int, summary: str, topics: List[str] = None) -> bool:
        """
        Mark a content item as AI processed and update its summary.

        Args:
            item_id: ID of the content item
            summary: AI-generated summary
            topics: Optional updated topics list

        Returns:
            True if update succeeded
        """
        update_dict = {
            ContentItem.ai_processed: True,
            ContentItem.summary: summary,
            ContentItem.updated_at: datetime.utcnow(),
        }

        if topics is not None:
            update_dict[ContentItem.topics] = topics

        result = self.db.query(ContentItem).filter(ContentItem.id == item_id).update(update_dict)
        self.db.commit()
        return result > 0

    def get_unclustered(self, hours_back: int = 48, limit: int = 500) -> List[ContentItem]:
        """Get content items without a cluster_id within the time window."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        return (
            self.db.query(ContentItem)
            .filter(ContentItem.cluster_id.is_(None), ContentItem.published_at >= cutoff)
            .order_by(desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )

    def get_by_cluster(
        self, cluster_id: str, content_type: Optional[ContentType] = None
    ) -> List[ContentItem]:
        """Get all content items in a cluster, optionally filtered by type."""
        query = self.db.query(ContentItem).filter(ContentItem.cluster_id == cluster_id)

        if content_type:
            query = query.filter(ContentItem.type == content_type)

        return query.order_by(desc(ContentItem.global_score)).all()

    def get_canonical_for_cluster(
        self, cluster_id: str, content_type: ContentType
    ) -> Optional[ContentItem]:
        """Get the canonical item for a cluster and type combination."""
        return (
            self.db.query(ContentItem)
            .filter(
                ContentItem.cluster_id == cluster_id,
                ContentItem.type == content_type,
                ContentItem.is_cluster_canonical == 1,
            )
            .first()
        )

    def get_recent_with_scores(self, hours_back: int = 72, limit: int = 1000) -> List[ContentItem]:
        """Get recent content items for scoring updates."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        return (
            self.db.query(ContentItem)
            .filter(ContentItem.published_at >= cutoff)
            .order_by(desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )

    def update_scores(
        self,
        item_id: int,
        quality_score: float,
        trend_score: float,
        recency_score: float,
        diversity_boost: float,
        global_score: float,
    ) -> bool:
        """Update all scores for a content item."""
        result = (
            self.db.query(ContentItem)
            .filter(ContentItem.id == item_id)
            .update(
                {
                    ContentItem.quality_score: quality_score,
                    ContentItem.trend_score: trend_score,
                    ContentItem.recency_score: recency_score,
                    ContentItem.diversity_boost: diversity_boost,
                    ContentItem.global_score: global_score,
                    ContentItem.updated_at: datetime.utcnow(),
                }
            )
        )
        self.db.commit()
        return result > 0

    def set_cluster(self, item_id: int, cluster_id: str, is_canonical: bool = False) -> bool:
        """Assign a content item to a cluster."""
        result = (
            self.db.query(ContentItem)
            .filter(ContentItem.id == item_id)
            .update(
                {
                    ContentItem.cluster_id: cluster_id,
                    ContentItem.is_cluster_canonical: 1 if is_canonical else 0,
                    ContentItem.updated_at: datetime.utcnow(),
                }
            )
        )
        self.db.commit()
        return result > 0

    def get_items_for_playlist(
        self,
        content_type: ContentType,
        hours_back: int = 72,
        limit: int = 100,
        exclude_cluster_ids: Optional[List[str]] = None,
        ai_processed_only: bool = True,
    ) -> List[ContentItem]:
        """
        Get content items for playlist generation.

        Returns canonical items only (for clustered content) or all items
        (for unclustered content), excluding specified clusters.

        Args:
            content_type: Type of content to retrieve
            hours_back: Time window in hours
            limit: Maximum items to return
            exclude_cluster_ids: Cluster IDs to exclude
            ai_processed_only: Only return AI-processed content (default True)
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        type_filter = ContentItem.type == content_type
        if content_type == ContentType.VIDEO:
            type_filter = surface_content_filter("videos")
        elif content_type == ContentType.REEL:
            type_filter = surface_content_filter("reels")

        query = self.db.query(ContentItem).filter(
            type_filter,
            visible_promotion_filter(),
            ContentItem.published_at >= cutoff,
            ContentItem.is_suppressed.is_(False),
            ContentItem.curation_status == ContentStatus.PROMOTED,
            # Include canonical items OR items without clusters
            or_(ContentItem.cluster_id.is_(None), ContentItem.is_cluster_canonical == 1),
            _ENGLISH_FILTER,
        )

        # Filter for AI-processed content only (unless explicitly disabled)
        if ai_processed_only:
            query = query.filter(ContentItem.ai_processed.is_(True))

        if exclude_cluster_ids:
            query = query.filter(~ContentItem.cluster_id.in_(exclude_cluster_ids))

        return (
            query.order_by(desc(ContentItem.global_score), desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )

    def get_topic_distribution(
        self, content_type: ContentType, hours_back: int = 24
    ) -> List[Tuple[str, int]]:
        """Get topic distribution for diversity calculation."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # Query items and aggregate topics
        items = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.type == content_type,
                ContentItem.published_at >= cutoff,
                _ENGLISH_FILTER,
            )
            .all()
        )

        topic_counts = {}
        for item in items:
            for topic in item.topics or []:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

        return sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)

    def bulk_create(self, items_data: List[dict]) -> List[ContentItem]:
        """Create multiple content items efficiently."""
        items = [ContentItem(**data) for data in items_data]
        self.db.bulk_save_objects(items)
        self.db.commit()
        return items

    def get_similar_items(
        self, item: ContentItem, hours_back: int = 48, limit: int = 50
    ) -> List[ContentItem]:
        """
        Get potentially similar items for clustering.

        Uses entity overlap and time proximity for candidate selection.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # Get items in the time window (excluding the item itself)
        return (
            self.db.query(ContentItem)
            .filter(
                ContentItem.id != item.id,
                ContentItem.published_at >= cutoff,
                ContentItem.published_at <= item.published_at + timedelta(hours=hours_back),
                ContentItem.published_at >= item.published_at - timedelta(hours=hours_back),
            )
            .order_by(desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )

    # --- Cluster aggregation methods (replaces ContentClusterRepository) ---

    def get_cluster_item_count(self, cluster_id: str) -> int:
        """
        Get the number of items in a cluster by aggregating content_items.

        This replaces the separate content_clusters table.
        """
        if not cluster_id:
            return 0

        count = (
            self.db.query(func.count(ContentItem.id))
            .filter(ContentItem.cluster_id == cluster_id)
            .scalar()
        )

        return count or 0

    def get_cluster_stats(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """
        Compute cluster statistics from content_items aggregation.

        Returns:
            Dictionary with item_count, first_seen, last_seen, or None if cluster doesn't exist
        """
        if not cluster_id:
            return None

        stats = (
            self.db.query(
                func.count(ContentItem.id).label("item_count"),
                func.min(ContentItem.published_at).label("first_seen"),
                func.max(ContentItem.published_at).label("last_seen"),
            )
            .filter(ContentItem.cluster_id == cluster_id)
            .first()
        )

        if not stats or stats.item_count == 0:
            return None

        return {
            "item_count": stats.item_count,
            "first_seen": stats.first_seen,
            "last_seen": stats.last_seen,
        }

    def get_active_cluster_ids(self, hours_back: int = 48) -> List[str]:
        """
        Get unique cluster IDs from recent content.

        Returns list of cluster_ids that have content within the time window.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        result = (
            self.db.query(ContentItem.cluster_id)
            .filter(ContentItem.cluster_id.isnot(None), ContentItem.published_at >= cutoff)
            .distinct()
            .all()
        )

        return [r[0] for r in result if r[0]]

    def get_cluster_items(self, cluster_id: str, limit: int = 50) -> List[ContentItem]:
        """Get all items in a cluster."""
        if not cluster_id:
            return []

        return (
            self.db.query(ContentItem)
            .filter(ContentItem.cluster_id == cluster_id)
            .order_by(desc(ContentItem.published_at))
            .limit(limit)
            .all()
        )
