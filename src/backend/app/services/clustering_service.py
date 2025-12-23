"""
Clustering service for grouping related content.

This service implements the MVP clustering approach:
- Match on entity overlap
- Title similarity
- Topic match
- Time proximity

Goal: Prevent showing the same story repeatedly across feeds.

NOTE: Clusters are implicit - stored as cluster_id on content_items.
Cluster metadata is computed on-the-fly via aggregation.
"""

import hashlib
import uuid
from typing import List, Optional, Set, Tuple, Dict
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.core.logging import get_logger
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentItem, ContentType

logger = get_logger(__name__)


# Clustering configuration
CLUSTER_WINDOW_HOURS = 48  # Time window for clustering
MIN_ENTITY_OVERLAP = 0.3  # Minimum entity overlap ratio
MIN_TITLE_SIMILARITY = 0.6  # Minimum title similarity ratio
MIN_TOPIC_OVERLAP = 0.4  # Minimum topic overlap ratio
COMBINED_THRESHOLD = 0.5  # Combined score threshold for clustering


class ClusteringService:
    """
    Service for clustering related content items.
    
    Clusters content about the same story to prevent repetition
    in user feeds while maintaining coverage of all sources.
    
    NOTE: Clusters are implicit via cluster_id on ContentItem.
    No separate cluster table - metadata is aggregated on demand.
    """
    
    def __init__(
        self,
        content_repo: ContentItemRepository
    ):
        self.content_repo = content_repo
    
    def cluster_new_item(self, item: ContentItem) -> Optional[str]:
        """
        Find or create a cluster for a new content item.
        
        Args:
            item: The content item to cluster
            
        Returns:
            cluster_id if assigned, None otherwise
        """
        # Get potential similar items within the time window
        candidates = self.content_repo.get_similar_items(
            item,
            hours_back=CLUSTER_WINDOW_HOURS,
            limit=100
        )
        
        if not candidates:
            return None
        
        # Find best matching cluster
        best_cluster_id = None
        best_score = 0.0
        
        for candidate in candidates:
            score = self._compute_similarity(item, candidate)
            
            if score >= COMBINED_THRESHOLD and score > best_score:
                best_score = score
                best_cluster_id = candidate.cluster_id
        
        if best_cluster_id:
            # Add to existing cluster
            self._add_to_cluster(item, best_cluster_id)
            logger.info(f"Added item {item.id} to existing cluster {best_cluster_id}")
            return best_cluster_id
        
        # Check if we should create a new cluster with a matching candidate
        for candidate in candidates:
            score = self._compute_similarity(item, candidate)
            
            if score >= COMBINED_THRESHOLD:
                # Create new cluster with both items
                cluster_id = self._create_cluster([item, candidate])
                logger.info(f"Created new cluster {cluster_id} with items {item.id}, {candidate.id}")
                return cluster_id
        
        return None
    
    def run_clustering_job(self) -> Dict[str, int]:
        """
        Run clustering on all unclustered items.
        
        Returns:
            Statistics about the clustering run
        """
        stats = {
            "processed": 0,
            "clustered": 0,
            "new_clusters": 0,
            "existing_clusters": 0
        }
        
        unclustered = self.content_repo.get_unclustered(
            hours_back=CLUSTER_WINDOW_HOURS,
            limit=500
        )
        
        logger.info(f"Running clustering on {len(unclustered)} unclustered items")
        
        for item in unclustered:
            stats["processed"] += 1
            
            cluster_id = self.cluster_new_item(item)
            
            if cluster_id:
                stats["clustered"] += 1
        
        # Update canonical items for all active clusters
        self._update_canonical_items()
        
        return stats
    
    def _compute_similarity(
        self,
        item1: ContentItem,
        item2: ContentItem
    ) -> float:
        """
        Compute similarity score between two content items.
        
        Uses weighted combination of:
        - Entity overlap (40%)
        - Title similarity (40%)
        - Topic overlap (20%)
        
        Returns:
            Similarity score between 0 and 1
        """
        # Entity overlap
        entity_score = self._entity_overlap(item1.entities or [], item2.entities or [])
        
        # Title similarity
        title_score = self._title_similarity(item1.title, item2.title)
        
        # Topic overlap
        topic_score = self._topic_overlap(item1.topics or [], item2.topics or [])
        
        # Weighted combination
        combined_score = (
            0.40 * entity_score +
            0.40 * title_score +
            0.20 * topic_score
        )
        
        return combined_score
    
    def _entity_overlap(
        self,
        entities1: List[dict],
        entities2: List[dict]
    ) -> float:
        """
        Compute entity overlap ratio.
        
        Entities are dicts with 'name' key.
        """
        if not entities1 or not entities2:
            return 0.0
        
        names1 = {e.get("name", "").lower() for e in entities1 if e.get("name")}
        names2 = {e.get("name", "").lower() for e in entities2 if e.get("name")}
        
        if not names1 or not names2:
            return 0.0
        
        intersection = len(names1 & names2)
        union = len(names1 | names2)
        
        return intersection / union if union > 0 else 0.0
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        Compute title similarity using sequence matching.
        
        Normalizes titles to lowercase and removes common prefixes.
        """
        # Normalize titles
        t1 = self._normalize_title(title1)
        t2 = self._normalize_title(title2)
        
        return SequenceMatcher(None, t1, t2).ratio()
    
    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison."""
        # Convert to lowercase
        title = title.lower()
        
        # Remove common prefixes/suffixes
        remove_patterns = [
            "breaking:",
            "update:",
            "exclusive:",
            "opinion:",
            "| techcrunch",
            "| the verge",
            "- wired",
            "- ars technica",
        ]
        
        for pattern in remove_patterns:
            title = title.replace(pattern, "")
        
        return title.strip()
    
    def _topic_overlap(
        self,
        topics1: List[str],
        topics2: List[str]
    ) -> float:
        """Compute topic overlap ratio (Jaccard index)."""
        if not topics1 or not topics2:
            return 0.0
        
        set1 = {t.lower() for t in topics1}
        set2 = {t.lower() for t in topics2}
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    def _generate_cluster_id(self) -> str:
        """Generate a unique cluster ID."""
        return str(uuid.uuid4())[:8]
    
    def _create_cluster(self, items: List[ContentItem]) -> str:
        """
        Create a new cluster by assigning cluster_id to items.
        
        NOTE: No separate cluster record is created. Clusters are implicit
        via the cluster_id field on content_items.
        """
        cluster_id = self._generate_cluster_id()
        
        # Assign items to cluster
        for item in items:
            self.content_repo.set_cluster(item.id, cluster_id, is_canonical=False)
        
        return cluster_id
    
    def _add_to_cluster(self, item: ContentItem, cluster_id: str) -> None:
        """
        Add an item to an existing cluster.
        
        Simply assigns the cluster_id to the item.
        """
        self.content_repo.set_cluster(item.id, cluster_id, is_canonical=False)
    
    def _update_canonical_items(self) -> None:
        """
        Update canonical items for all active clusters.
        
        For each cluster and content type, the item with the highest
        global_score is marked as canonical.
        """
        active_cluster_ids = self.content_repo.get_active_cluster_ids(
            hours_back=CLUSTER_WINDOW_HOURS
        )
        
        for cluster_id in active_cluster_ids:
            for content_type in ContentType:
                items = self.content_repo.get_by_cluster(cluster_id, content_type)
                
                if not items:
                    continue
                
                # Reset all to non-canonical
                for item in items:
                    if item.is_cluster_canonical == 1:
                        self.content_repo.set_cluster(item.id, cluster_id, is_canonical=False)
                
                # Mark highest scoring as canonical
                best_item = max(items, key=lambda x: x.global_score)
                self.content_repo.set_cluster(best_item.id, cluster_id, is_canonical=True)
                
                logger.debug(
                    f"Cluster {cluster_id} type {content_type.value}: "
                    f"canonical item {best_item.id}"
                )
    
    def get_cluster_stats(self) -> Dict[str, any]:
        """Get statistics about clustering."""
        active_cluster_ids = self.content_repo.get_active_cluster_ids(
            hours_back=CLUSTER_WINDOW_HOURS
        )
        
        total_items = 0
        cluster_sizes = []
        
        for cluster_id in active_cluster_ids:
            item_count = self.content_repo.get_cluster_item_count(cluster_id)
            cluster_sizes.append(item_count)
            total_items += item_count
        
        return {
            "active_clusters": len(active_cluster_ids),
            "total_clustered_items": total_items,
            "avg_cluster_size": sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0,
            "max_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
            "min_cluster_size": min(cluster_sizes) if cluster_sizes else 0,
        }


def compute_dedupe_key(title: str, source: str) -> str:
    """
    Compute a deduplication key for content.
    
    Uses normalized title + source to detect exact duplicates.
    """
    normalized = f"{title.lower().strip()}:{source.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()[:16]
