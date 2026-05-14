"""
Clustering service.

Orchestrates the clustering job that groups related content.
This is a thin service that coordinates the clustering modules.
"""

import uuid
from typing import Dict, List, Optional

from app.clustering.dedupe import is_near_duplicate_simhash
from app.clustering.similarity import (
    compute_entity_overlap,
    compute_similarity,
    compute_title_similarity,
)
from app.clustering.url_normalize import canonical_cluster_url
from app.config.clustering import clustering_config
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository

logger = get_logger(__name__)

# Ordered list of primary/authoritative AI sources used when electing a cluster
# representative.  Higher index = lower priority (first entry wins ties).
_AI_SOURCE_PRIORITY: Dict[str, float] = {
    # Primary lab blogs — most authoritative
    "OpenAI Blog": 1.00,
    "Anthropic Blog": 1.00,
    "Google DeepMind Blog": 1.00,
    "Meta AI Blog": 0.98,
    "Microsoft AI Blog": 0.95,
    "Hugging Face Blog": 0.95,
    # Academic research
    "Stanford HAI": 0.92,
    "MIT CSAIL": 0.90,
    "Papers With Code": 0.88,
    # Infrastructure
    "SemiAnalysis": 0.88,
    "NVIDIA AI Blog": 0.75,
    "AWS Machine Learning Blog": 0.72,
    "Azure AI Blog": 0.70,
    # High-quality general tech (partial AI coverage)
    "MIT Technology Review": 0.80,
    "Simon Willison's Blog": 0.82,
    # Default fallback — anything not in this map gets 0.50
}


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

        # ------------------------------------------------------------------
        # URL fast-path (articles only): if any candidate shares the same
        # canonical URL key (host + path, no query params), treat them as the
        # same article and cluster immediately without running the heavier
        # similarity scorer.  This catches cross-source syndication where
        # tracking params differ but the underlying article path is identical.
        #
        # Videos are intentionally excluded: YouTube watch URLs canonicalize
        # to the same key when query params are dropped, which would
        # incorrectly cluster unrelated videos.
        # ------------------------------------------------------------------
        item_url_key = (
            canonical_cluster_url(getattr(item, "source_url", None))
            if item.type == ContentType.ARTICLE
            else None
        )
        if item_url_key:
            url_matches = [
                c
                for c in candidates
                if canonical_cluster_url(getattr(c, "source_url", None)) == item_url_key
            ]
            if url_matches:
                # Prefer a match that already belongs to a cluster so we join
                # an existing group rather than creating a redundant new one.
                clustered_match = next((c for c in url_matches if c.cluster_id), None)
                url_match = clustered_match or url_matches[0]

                if url_match.cluster_id:
                    self._add_to_cluster(item, url_match.cluster_id)
                    logger.info(
                        "[clustering] url_fast_path: item %s → existing cluster %s (url_key=%r)",
                        item.id,
                        url_match.cluster_id,
                        item_url_key,
                    )
                    return url_match.cluster_id
                else:
                    cluster_id = self._create_cluster([item, url_match])
                    logger.info(
                        "[clustering] url_fast_path: created cluster %s for items "
                        "%s, %s (url_key=%r)",
                        cluster_id,
                        item.id,
                        url_match.id,
                        item_url_key,
                    )
                    return cluster_id

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
            if self._is_near_duplicate(item, candidate):
                score = max(score, clustering_config.combined_threshold + 0.10)

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
            if self._is_near_duplicate(item, candidate):
                score = max(score, clustering_config.combined_threshold + 0.10)

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

    @staticmethod
    def _entity_overlap(entities1: List, entities2: List) -> float:
        """Backward-compatible wrapper for legacy unit tests."""
        return compute_entity_overlap(entities1, entities2)

    @staticmethod
    def _title_similarity(title1: str, title2: str) -> float:
        """Backward-compatible wrapper for legacy unit tests."""
        return compute_title_similarity(title1, title2)

    @staticmethod
    def _compute_similarity(item1: ContentItem, item2: ContentItem) -> float:
        """Backward-compatible wrapper for legacy unit tests."""
        return compute_similarity(
            item1_entities=item1.entities or [],
            item1_title=item1.title,
            item1_topics=item1.topics or [],
            item2_entities=item2.entities or [],
            item2_title=item2.title,
            item2_topics=item2.topics or [],
        )

    def _update_canonical_items(self) -> None:
        """
        Update canonical items for all active clusters.

        For each cluster and content type, the item with the highest
        ``global_score`` is marked as canonical.  For AI-category clusters
        (primary topic == "AI"), the score is augmented by an authoritative-
        source bonus so that e.g. the OpenAI Blog is preferred over an
        aggregator that republished the same story.
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

                # Mark highest scoring as canonical (with AI source bonus)
                best_item = max(items, key=lambda x: self._canonical_score(x))
                self.content_repo.set_cluster(best_item.id, cluster_id, is_canonical=True)

    @staticmethod
    def _canonical_score(item: ContentItem) -> float:
        """
        Compute a composite score for canonical-item election.

        For AI-category items the ``global_score`` is blended with a small
        authoritative-source bonus (up to +0.05) so that primary lab blogs
        win ties over aggregators without completely overriding quality.
        """
        topics = item.topics or []
        primary_topic = topics[0] if topics else None

        # Unscored items get 0.0 regardless of topic — don't award AI bonus to
        # items that have never been quality-scored.
        if item.global_score is None:
            return 0.0

        base = item.global_score

        if primary_topic == "AI":
            source_priority = _AI_SOURCE_PRIORITY.get(item.source, 0.50)
            # Blend: 95 % quality, 5 % source authority
            return base * 0.95 + source_priority * 0.05

        return base

    @staticmethod
    def _is_near_duplicate(item: ContentItem, candidate: ContentItem) -> bool:
        """Near-duplicate check using title simhash for article items."""
        if item.type != ContentType.ARTICLE or candidate.type != ContentType.ARTICLE:
            return False
        return is_near_duplicate_simhash(
            item.simhash,
            candidate.simhash,
            max_distance=3,
        )

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
