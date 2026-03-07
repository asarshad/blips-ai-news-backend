"""
Playlist service for session-based content delivery.

Generates personalized playlists with constraints:
- No repeated cluster_id in same playlist
- Topic dominance ≤ 40%
- Source rotation (max 3 consecutive from same source)
- Mix of canonical and fresh items

Uses Redis for caching playlists per session.

NOTE: Playlists are session snapshots. Once generated, the playlist
is immutable for that session. Use cursor-based continuation rather
than offset pagination for swipe feeds.
"""

import hashlib
import json
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import UserPreferenceRepository, UserProfileRepository
from app.services.multi_factor_ranking_service import MultiFactorRankingService
from app.services.personalization_service import PersonalizationService

logger = get_logger(__name__)

settings = get_settings()


# Playlist configuration
DEFAULT_PLAYLIST_SIZE = 50
MAX_PLAYLIST_SIZE = 100
MIN_PLAYLIST_SIZE = 20

# Diversity constraints
MAX_TOPIC_DOMINANCE = 0.40  # 40% max for any single topic
MAX_CONSECUTIVE_SAME_SOURCE = 3
MIN_UNIQUE_SOURCES = 3

# Content freshness
MAX_CONTENT_AGE_HOURS = 72  # 3 days
PREFER_CANONICAL_WEIGHT = 0.7  # 70% canonical, 30% fresh

# Cache configuration
PLAYLIST_CACHE_TTL_SECONDS = 300  # 5 minutes
PLAYLIST_CACHE_PREFIX = "playlist:"
SESSION_SNAPSHOT_TTL_SECONDS = 3600  # 1 hour for session snapshots


class PlaylistService:
    """
    Service for generating and caching session playlists.

    Creates personalized content playlists with diversity
    constraints, using Redis for caching.

    NOTE: Playlists are session snapshots. The cursor/position
    is tracked per session, not via offset pagination.
    """

    def __init__(
        self,
        content_repo: ContentItemRepository,
        profile_repo: UserProfileRepository,
        preference_repo: UserPreferenceRepository,
        personalization_service: PersonalizationService,
        ranking_service: Optional[MultiFactorRankingService] = None,
        redis_client=None,
    ):
        self.content_repo = content_repo
        self.profile_repo = profile_repo
        self.preference_repo = preference_repo
        self.personalization = personalization_service
        self.ranking_service = ranking_service or MultiFactorRankingService()
        self.redis = redis_client

    def get_playlist(
        self,
        device_id: str,
        content_type: ContentType,
        size: int = DEFAULT_PLAYLIST_SIZE,
        session_id: Optional[str] = None,
        cursor: Optional[int] = None,
        force_refresh: bool = False,
    ) -> Dict:
        """
        Get a playlist for a user session.

        Uses session snapshots: once generated, a playlist is immutable
        for that session. Cursor-based continuation (not offset pagination)
        ensures stable feeds when swiping.

        Args:
            device_id: User device ID
            content_type: Type of content (ARTICLE, VIDEO, REEL)
            size: Number of items to return per request
            session_id: Optional session ID for continuity (auto-generated if not provided)
            cursor: Position in the playlist to continue from (0-indexed)
            force_refresh: Force regenerate playlist (creates new session)

        Returns:
            Dictionary with items, session_id, cursor, and has_more
        """
        # Clamp size
        size = max(MIN_PLAYLIST_SIZE, min(MAX_PLAYLIST_SIZE, size))

        # Use existing session or create new one
        if force_refresh or not session_id:
            session_id = str(uuid.uuid4())[:8]

        # Get or generate session snapshot
        cache_key = self._get_session_cache_key(device_id, content_type, session_id)

        playlist = None
        if self.redis:
            playlist = self._get_from_cache(cache_key)

        if not playlist:
            # Generate new playlist snapshot
            playlist = self._generate_playlist(device_id, content_type, MAX_PLAYLIST_SIZE)

            # Cache the session snapshot
            if self.redis:
                self._set_session_cache(cache_key, playlist)

        # Cursor-based pagination
        start_cursor = cursor or 0
        items = playlist[start_cursor : start_cursor + size]
        next_cursor = start_cursor + len(items) if len(items) > 0 else None
        has_more = start_cursor + len(items) < len(playlist)

        return {
            "items": items,
            "session_id": session_id,
            "cursor": next_cursor,
            "has_more": has_more,
            "total_items": len(playlist),
        }

    def get_playlist_legacy(
        self,
        device_id: str,
        content_type: ContentType,
        size: int = DEFAULT_PLAYLIST_SIZE,
        offset: int = 0,
        force_refresh: bool = False,
    ) -> List[Dict]:
        """
        Legacy offset-based pagination (deprecated).

        Prefer get_playlist() with session_id and cursor for swipe feeds.
        """
        result = self.get_playlist(
            device_id=device_id,
            content_type=content_type,
            size=size,
            cursor=offset,
            force_refresh=force_refresh,
        )
        return result["items"]

    def _generate_playlist(
        self, device_id: str, content_type: ContentType, size: int
    ) -> List[Dict]:
        """Generate a new playlist with diversity constraints."""
        # Get candidate items
        candidates = self._get_candidates(content_type)

        if not candidates:
            logger.warning(f"No candidates found for {content_type}")
            return []

        # Score candidates with personalization
        scored_candidates = self._score_candidates(device_id, candidates)

        # Select items with diversity constraints
        selected = self._select_diverse_items(scored_candidates, size)

        # Convert to response format
        return [self._format_item(item) for item in selected]

    def _get_candidates(self, content_type: ContentType) -> List[ContentItem]:
        """Get candidate items for playlist."""
        # Get canonical items from clusters (repo already filters for canonical)
        candidates = self.content_repo.get_items_for_playlist(
            content_type=content_type, hours_back=MAX_CONTENT_AGE_HOURS, limit=500
        )

        # Deduplicate by ID
        seen = set()
        unique = []
        for item in candidates:
            if item.id not in seen:
                seen.add(item.id)
                unique.append(item)

        return unique

    def _score_candidates(
        self, device_id: str, candidates: List[ContentItem]
    ) -> List[Tuple[ContentItem, float]]:
        """Score candidates with multi-factor ranking."""
        scored = []

        for item in candidates:
            personalization = self.personalization.compute_personalization_score(device_id, item)
            final_score = self.ranking_service.score_item(
                item, personalization_score=personalization
            )
            scored.append((item, final_score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored

    def _select_diverse_items(
        self, scored_candidates: List[Tuple[ContentItem, float]], size: int
    ) -> List[ContentItem]:
        """Select items while maintaining diversity constraints."""
        selected = []
        used_clusters: Set[int] = set()
        topic_counts: Dict[str, int] = defaultdict(int)
        source_streak: List[str] = []

        for item, _score in scored_candidates:
            if len(selected) >= size:
                break

            # Check cluster constraint
            if item.cluster_id and item.cluster_id in used_clusters:
                continue

            # Check topic dominance
            if not self._check_topic_diversity(item, topic_counts, len(selected)):
                continue

            # Check source rotation
            if not self._check_source_rotation(item, source_streak):
                continue

            # Add item
            selected.append(item)

            if item.cluster_id:
                used_clusters.add(item.cluster_id)

            # Update topic counts
            if item.topics:
                for topic in item.topics:
                    topic_counts[topic.lower()] += 1

            # Update source streak
            source_streak.append(item.source.lower())
            if len(source_streak) > MAX_CONSECUTIVE_SAME_SOURCE:
                source_streak.pop(0)

        return selected

    def _check_topic_diversity(
        self, item: ContentItem, topic_counts: Dict[str, int], current_size: int
    ) -> bool:
        """Check if adding item would violate topic diversity."""
        if current_size == 0 or not item.topics:
            return True

        # Check each topic
        for topic in item.topics:
            current_count = topic_counts.get(topic.lower(), 0)
            future_share = (current_count + 1) / (current_size + 1)

            if future_share > MAX_TOPIC_DOMINANCE:
                return False

        return True

    def _check_source_rotation(self, item: ContentItem, source_streak: List[str]) -> bool:
        """Check if adding item would violate source rotation."""
        if len(source_streak) < MAX_CONSECUTIVE_SAME_SOURCE:
            return True

        # Check if all recent items are from same source
        recent = source_streak[-MAX_CONSECUTIVE_SAME_SOURCE:]
        if len(set(recent)) == 1 and recent[0] == item.source.lower():
            return False

        return True

    def _format_item(self, item: ContentItem) -> Dict:
        """Format content item for API response."""
        return {
            "id": item.id,
            "type": item.type.value,
            "source": item.source,
            "source_url": item.source_url,
            "title": item.title,
            "description": item.description,
            "summary": item.summary,
            "image_url": item.image_url,
            "video_url": item.video_url,
            "duration": item.duration_seconds,
            "topics": item.topics or [],
            "entities": item.entities or [],
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "global_score": item.global_score,
            "cluster_id": item.cluster_id,
        }

    def _paginate(self, items: List[Dict], offset: int, size: int) -> List[Dict]:
        """Paginate playlist items."""
        start = max(0, offset)
        end = start + size
        return items[start:end]

    def _get_cache_key(self, device_id: str, content_type: ContentType) -> str:
        """Generate cache key for playlist."""
        # Use hash of device_id for privacy
        device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
        return f"{PLAYLIST_CACHE_PREFIX}{device_hash}:{content_type.value}"

    def _get_session_cache_key(
        self, device_id: str, content_type: ContentType, session_id: str
    ) -> str:
        """Generate cache key for session snapshot."""
        device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
        return f"{PLAYLIST_CACHE_PREFIX}session:{device_hash}:{content_type.value}:{session_id}"

    def _get_from_cache(self, key: str) -> Optional[List[Dict]]:
        """Get playlist from Redis cache."""
        try:
            data = self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
        return None

    def _set_cache(self, key: str, playlist: List[Dict]):
        """Set playlist in Redis cache (short TTL for legacy)."""
        try:
            self.redis.setex(key, PLAYLIST_CACHE_TTL_SECONDS, json.dumps(playlist))
        except Exception as e:
            logger.warning(f"Cache set error: {e}")

    def _set_session_cache(self, key: str, playlist: List[Dict]):
        """Set session snapshot in Redis cache (longer TTL)."""
        try:
            self.redis.setex(key, SESSION_SNAPSHOT_TTL_SECONDS, json.dumps(playlist))
        except Exception as e:
            logger.warning(f"Session cache set error: {e}")

    def invalidate_user_cache(self, device_id: str):
        """Invalidate all cached playlists for a user."""
        if not self.redis:
            return

        device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
        pattern = f"{PLAYLIST_CACHE_PREFIX}{device_hash}:*"

        try:
            keys = self.redis.keys(pattern)
            if keys:
                self.redis.delete(*keys)
                logger.debug(f"Invalidated {len(keys)} playlist caches for user")
        except Exception as e:
            logger.warning(f"Cache invalidation error: {e}")

    def get_playlist_stats(self) -> Dict[str, any]:
        """Get statistics about playlist generation."""
        stats = {"by_type": {}}

        for content_type in ContentType:
            candidates = self._get_candidates(content_type)

            if not candidates:
                continue

            # Analyze candidates
            cluster_count = len(set(c.cluster_id for c in candidates if c.cluster_id))
            source_count = len(set(c.source.lower() for c in candidates))
            avg_score = sum(c.global_score or 0 for c in candidates) / len(candidates)

            stats["by_type"][content_type.value] = {
                "candidate_count": len(candidates),
                "unique_clusters": cluster_count,
                "unique_sources": source_count,
                "avg_global_score": round(avg_score, 3),
            }

        return stats


def create_playlist_service(db_session, redis_client=None) -> PlaylistService:
    """Factory function to create PlaylistService with dependencies."""
    from app.repositories.content_repo import ContentItemRepository
    from app.repositories.user_repo import (
        InteractionEventRepository,
        UserPreferenceRepository,
        UserProfileRepository,
    )
    from app.services.personalization_service import PersonalizationService

    content_repo = ContentItemRepository(db_session)
    profile_repo = UserProfileRepository(db_session)
    preference_repo = UserPreferenceRepository(db_session)
    event_repo = InteractionEventRepository(db_session)

    personalization = PersonalizationService(
        profile_repo, preference_repo, event_repo, content_repo
    )

    return PlaylistService(
        content_repo=content_repo,
        profile_repo=profile_repo,
        preference_repo=preference_repo,
        personalization_service=personalization,
        redis_client=redis_client,
    )
