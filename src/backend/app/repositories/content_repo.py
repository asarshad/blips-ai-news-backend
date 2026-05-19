"""
Repository for content items.

Provides data access methods for the unified content_items table.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType
from app.repositories.base import BaseRepository
from app.services.content_readiness import (
    ready_content_filter,
    surface_name_for_content_type,
    sync_content_readiness,
)

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

    def get_article_supply_counts_on_ingestion_day(self, day: date) -> Dict[str, int]:
        """Return promoted/ready article counts for the logical ingestion day."""

        from app.ingestion.time import get_ingestion_day_bounds

        start, end = get_ingestion_day_bounds(day=day)
        day_filter = or_(
            ContentItem.ingestion_day == day,
            and_(
                ContentItem.ingestion_day.is_(None),
                ContentItem.created_at >= start,
                ContentItem.created_at < end,
            ),
        )

        promoted = int(
            self.db.query(func.count(ContentItem.id))
            .filter(
                day_filter,
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
            )
            .scalar()
            or 0
        )
        ready = int(
            self.db.query(func.count(ContentItem.id))
            .filter(
                day_filter,
                ready_content_filter("articles"),
            )
            .scalar()
            or 0
        )
        return {"promoted": promoted, "ready": ready}

    def get_by_type(
        self,
        content_type: ContentType,
        limit: int = 50,
        offset: int = 0,
        hours_back: int = 72,
    ) -> List[ContentItem]:
        """
        Get recent content items of a specific type.

        Only client-ready items are returned. Promotion is necessary, but the
        shared readiness contract is the actual delivery gate.

        Args:
            content_type: ARTICLE, VIDEO, or REEL
            limit: Maximum number of items
            offset: Pagination offset
            hours_back: Only include items from the last N hours
        Returns:
            List of content items ordered by global_score
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        surface_name = surface_name_for_content_type(content_type)

        query = self.db.query(ContentItem).filter(
            ready_content_filter(surface_name),
            ContentItem.published_at >= cutoff,
            _ENGLISH_FILTER,
        )

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

    def get_recent_promoted_articles_pending_ai(
        self,
        *,
        limit: int = 100,
        lookback_days: int = 7,
    ) -> List[ContentItem]:
        """Return recent promoted articles that still need AI processing."""
        cutoff = datetime.utcnow() - timedelta(days=max(1, int(lookback_days)))
        return (
            self.db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.ai_processed.is_(False),
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff,
            )
            .order_by(desc(ContentItem.published_at), desc(ContentItem.id))
            .limit(max(limit, 1))
            .all()
        )

    def get_recent_promoted_videos_pending_ai(
        self,
        *,
        limit: int = 100,
        lookback_hours: int = 168,
    ) -> List[ContentItem]:
        """Return recent promoted videos that still need AI processing."""
        cutoff = datetime.utcnow() - timedelta(hours=max(1, int(lookback_hours)))
        return (
            self.db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.ai_processed.is_(False),
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff,
            )
            .order_by(desc(ContentItem.published_at), desc(ContentItem.id))
            .limit(max(limit, 1))
            .all()
        )

    def get_articles_with_short_summaries(
        self,
        *,
        limit: int = 100,
        hours_back: int = 168,
        max_words: int,
    ) -> List[ContentItem]:
        """Return promoted article items whose stored summary is shorter than the target."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        candidates = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.ai_processed.is_(True),
                ContentItem.summary.isnot(None),
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(desc(ContentItem.published_at))
            .limit(max(limit * 5, limit))
            .all()
        )

        short_items: List[ContentItem] = []
        for item in candidates:
            summary = (item.summary or "").strip()
            if summary and len(summary.split()) < max_words:
                short_items.append(item)
                if len(short_items) >= limit:
                    break

        return short_items

    def get_videos_with_short_summaries(
        self,
        *,
        limit: int = 100,
        hours_back: int = 168,
        min_words: int,
    ) -> List[ContentItem]:
        """Return promoted video items whose stored summary is shorter than the target."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        candidates = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.ai_processed.is_(True),
                ContentItem.summary.isnot(None),
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(desc(ContentItem.published_at))
            .limit(max(limit * 5, limit))
            .all()
        )

        short_items: List[ContentItem] = []
        for item in candidates:
            summary = (item.summary or "").strip()
            if summary and len(summary.split()) < min_words:
                short_items.append(item)
                if len(short_items) >= limit:
                    break

        return short_items

    def get_articles_with_long_summaries(
        self,
        *,
        limit: int = 100,
        hours_back: int | None = None,
        min_words: int,
    ) -> List[ContentItem]:
        """Return promoted article items whose stored summary is longer than the target."""
        query = self.db.query(ContentItem).filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.ai_processed.is_(True),
            ContentItem.summary.isnot(None),
            ContentItem.is_suppressed.is_(False),
            ContentItem.curation_status == ContentStatus.PROMOTED,
        )
        if hours_back is not None:
            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            query = query.filter(ContentItem.published_at >= cutoff)

        candidates = (
            query.order_by(desc(ContentItem.published_at)).limit(max(limit * 5, limit)).all()
        )

        long_items: List[ContentItem] = []
        for item in candidates:
            summary = (item.summary or "").strip()
            if summary and len(summary.split()) > min_words:
                long_items.append(item)
                if len(long_items) >= limit:
                    break

        return long_items

    def mark_ai_processed(
        self,
        item_id: int,
        summary: str,
        topics: List[str] = None,
        *,
        tech_relevance: Optional[str] = None,
        tech_relevance_confidence: Optional[float] = None,
        tech_relevance_reason: Optional[str] = None,
        is_major_tech_news: Optional[bool] = None,
        major_tech_news_confidence: Optional[float] = None,
        major_tech_news_reason: Optional[str] = None,
        audience_lane: Optional[str] = None,
        audience_lane_confidence: Optional[float] = None,
        audience_lane_reason: Optional[str] = None,
        commit: bool = True,
    ) -> bool:
        """
        Mark a content item as AI processed and update its summary.

        Args:
            item_id: ID of the content item
            summary: AI-generated summary
            topics: Optional updated topics list
            tech_relevance: "yes" | "no" from Blips tech relevance classifier
            tech_relevance_confidence: classifier confidence score
            tech_relevance_reason: classifier reason string

        Returns:
            True if update succeeded
        """
        item = self.db.query(ContentItem).filter(ContentItem.id == item_id).first()
        if item is None:
            return False

        item.ai_processed = True
        item.summary = summary
        item.updated_at = datetime.utcnow()
        if topics is not None:
            item.topics = topics
        if tech_relevance is not None:
            item.tech_relevance = tech_relevance
        if tech_relevance_confidence is not None:
            item.tech_relevance_confidence = tech_relevance_confidence
        if tech_relevance_reason is not None:
            item.tech_relevance_reason = tech_relevance_reason
        if is_major_tech_news is not None:
            item.is_major_tech_news = bool(is_major_tech_news)
        if major_tech_news_confidence is not None:
            item.major_tech_news_confidence = major_tech_news_confidence
        if major_tech_news_reason is not None:
            item.major_tech_news_reason = major_tech_news_reason
        if audience_lane is not None:
            item.audience_lane = audience_lane
        if audience_lane_confidence is not None:
            item.audience_lane_confidence = audience_lane_confidence
        if audience_lane_reason is not None:
            item.audience_lane_reason = audience_lane_reason
        sync_content_readiness(self.db, item)
        if commit:
            self.db.commit()
        return True

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
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        surface_name = surface_name_for_content_type(content_type)

        query = self.db.query(ContentItem).filter(
            ready_content_filter(surface_name),
            ContentItem.published_at >= cutoff,
            # Include canonical items OR items without clusters
            or_(ContentItem.cluster_id.is_(None), ContentItem.is_cluster_canonical == 1),
            _ENGLISH_FILTER,
        )

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

    def update_duration_seconds_bulk(self, updates: Dict[int, int]) -> int:
        """Persist best-effort video duration backfills for existing items."""
        normalized_updates = [
            {
                "id": item_id,
                "duration_seconds": int(duration_seconds),
                "updated_at": datetime.utcnow(),
            }
            for item_id, duration_seconds in updates.items()
            if item_id and isinstance(duration_seconds, (int, float)) and int(duration_seconds) > 0
        ]
        if not normalized_updates:
            return 0

        self.db.bulk_update_mappings(ContentItem, normalized_updates)
        self.db.commit()
        return len(normalized_updates)

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
