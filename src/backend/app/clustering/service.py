"""
Clustering service.

Orchestrates the clustering job that groups related content.
This is a thin service that coordinates the clustering modules.
"""

import uuid
from typing import Dict, List, Optional

from app.clustering.similarity import compute_similarity
from app.config.clustering import clustering_config
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository

logger = get_logger(__name__)


class ClusteringService:
    """
    Service for clustering related content items.
    
    Clusters content about the same story to prevent repetition
    in user feeds while maintaining coverage of all sources.
    
    NOTE: Clusters are implicit via cluster_id on ContentItem.
    No separate cluster table - metadata is aggregated on demand.
    """
    
    def __init__(self, content_repo: ContentItemRepository):
        self.content_repo = content_repo
    
    def cluster_new_item(self, item: ContentItem) -> Optional[str]:
        """
        Find or create a cluster for a new content item.
        
        Args:
            item: The content item to cluster
            
        Returns:
            cluster_id if assigned, None otherwise
        """
        candidates = self.content_repo.get_similar_items(
            item,
            hours_back=clustering_config.window_hours,
            limit=100,
        )
        
        if not candidates:
            return None
        
        # Find best matching cluster
        best_cluster_id = None
        best_score = 0.0
        
        for candidate in candidates:
            score = compute_similarity(
                item1_entities=item.entities or [],
                item1_title=item.title,
                item1_topics=item.topics or [],
                item2_entities=candidate.entities or [],
                item2_title=candidate.title,
                item2_topics=candidate.topics or [],
            )
            
            if score >= clustering_config.combined_threshold and score > best_score:
                best_score = score
                best_cluster_id = candidate.cluster_id
        
        if best_cluster_id:
            self._add_to_cluster(item, best_cluster_id)
            logger.info(f"Added item {item.id} to cluster {best_cluster_id}")
            return best_cluster_id
        
        # Check if we should create a new cluster
        for candidate in candidates:
            score = compute_similarity(
                item1_entities=item.entities or [],
                item1_title=item.title,
                item1_topics=item.topics or [],
                item2_entities=candidate.entities or [],
                item2_title=candidate.title,
                item2_topics=candidate.topics or [],
            )
            
            if score >= clustering_config.combined_threshold:
                cluster_id = self._create_cluster([item, candidate])
                logger.info(f"Created cluster {cluster_id} with items {item.id}, {candidate.id}")
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
            "existing_clusters": 0,
        }
        
        unclustered = self.content_repo.get_unclustered(
            hours_back=clustering_config.window_hours,
            limit=500,
        )
        
        logger.info(f"Running clustering on {len(unclustered)} items")
        
        for item in unclustered:
            stats["processed"] += 1
            cluster_id = self.cluster_new_item(item)
            if cluster_id:
                stats["clustered"] += 1
        
        self._update_canonical_items()
        return stats
    
    def _create_cluster(self, items: List[ContentItem]) -> str:
        """Create a new cluster with the given items."""
        cluster_id = str(uuid.uuid4())[:8]
        
        for item in items:
            self.content_repo.set_cluster(item.id, cluster_id, is_canonical=False)
        
        return cluster_id
    
    def _add_to_cluster(self, item: ContentItem, cluster_id: str) -> None:
        """Add an item to an existing cluster."""
        self.content_repo.set_cluster(item.id, cluster_id, is_canonical=False)
    
    def _update_canonical_items(self) -> None:
        """
        Update canonical items for all active clusters.
        
        For each cluster and content type, the item with the highest
        global_score is marked as canonical.
        """
        active_cluster_ids = self.content_repo.get_active_cluster_ids(
            hours_back=clustering_config.window_hours
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
    
    def get_cluster_stats(self) -> Dict[str, any]:
        """Get statistics about clustering."""
        active_cluster_ids = self.content_repo.get_active_cluster_ids(
            hours_back=clustering_config.window_hours
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
