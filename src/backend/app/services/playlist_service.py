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
from app.models.content import ContentItem, ContentType, EventType
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import (
    InteractionEventRepository,
    UserPreferenceRepository,
    UserProfileRepository,
)
from app.services.multi_factor_ranking_service import MultiFactorRankingService
from app.services.personalization_service import PersonalizationService
from app.services.video_duration_hydration import hydrate_missing_video_durations
from app.video_surface_rules import effective_content_type, has_explicit_shorts_url

logger = get_logger(__name__)

settings = get_settings()


# Playlist configuration
DEFAULT_PLAYLIST_SIZE = 50
MAX_PLAYLIST_SIZE = 100
MIN_PLAYLIST_SIZE = 20

# Diversity constraints
MAX_TOPIC_DOMINANCE = 0.40  # 40% max for any single topic
MAX_CONSECUTIVE_SAME_SOURCE = 3
SOURCE_CAP_WINDOW_SIZE = 5
MAX_SOURCE_PER_WINDOW = 2
CATEGORY_CAP_WINDOW_SIZE = 5
MAX_CATEGORY_SHARE_PER_WINDOW = 0.40
MIN_UNIQUE_SOURCES = 3

# Content freshness
MAX_CONTENT_AGE_HOURS = 72  # 3 days
FALLBACK_CONTENT_AGE_HOURS = 168  # 7 days fallback when no fresh approvals
PREFER_CANONICAL_WEIGHT = 0.7  # 70% canonical, 30% fresh
CONSUMED_SUPPRESSION_HOURS = 24
EXPOSED_DEMOTION_HOURS = 6
EXPOSED_ONLY_DEMOTION_MULTIPLIER = 0.65
NEGATIVE_ITEM_SUPPRESSION_HOURS = 24
NEGATIVE_CREATOR_SUPPRESSION_HOURS = 168

ARTICLE_CONSUMED_EVENTS = {
    EventType.OPEN_SOURCE,
    EventType.SHARE,
    EventType.SAVE,
    EventType.CHAT_START,
    EventType.CHAT_MESSAGE,
}
VIDEO_CONSUMED_EVENTS = ARTICLE_CONSUMED_EVENTS | {
    EventType.VIDEO_SAVE,
    EventType.VIDEO_SHARE,
    EventType.VIDEO_50PCT,
    EventType.VIDEO_95PCT,
}
VIDEO_EXPOSED_EVENTS = {EventType.VIDEO_IMPRESSION}

# Cache configuration
PLAYLIST_CACHE_TTL_SECONDS = 300  # 5 minutes
PLAYLIST_CACHE_PREFIX = "playlist:"
VIDEO_REEL_PLAYLIST_CACHE_PREFIX = "playlist:video-reel-v3:"
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
        interaction_repo: Optional[InteractionEventRepository] = None,
        ranking_service: Optional[MultiFactorRankingService] = None,
        redis_client=None,
    ):
        self.content_repo = content_repo
        self.profile_repo = profile_repo
        self.preference_repo = preference_repo
        self.interaction_repo = interaction_repo
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
            if playlist and not self._is_cache_compatible(playlist, content_type):
                logger.info(
                    "Discarding stale session playlist cache for %s; incompatible payload",
                    content_type.value,
                )
                playlist = None

        if not playlist:
            # Generate new playlist snapshot
            playlist_items = self._generate_playlist_items(
                device_id, content_type, MAX_PLAYLIST_SIZE
            )

            if playlist_items and len(playlist_items) < MIN_PLAYLIST_SIZE:
                logger.info(
                    "Fresh playlist for %s too small (%s items) - topping up from historical window",
                    content_type.value,
                    len(playlist_items),
                )
                playlist_items = self._top_up_items_with_historical(
                    device_id=device_id,
                    content_type=content_type,
                    selected_items=playlist_items,
                    target_size=MAX_PLAYLIST_SIZE,
                )

            duration_overrides = self._hydrate_duration_overrides(playlist_items)
            playlist = [
                self._format_item(item, duration_overrides=duration_overrides)
                for item in playlist_items
            ]

            # If no new approved content is available, keep serving the prior feed.
            if not playlist:
                playlist = self._load_fallback_playlist(device_id, content_type)

            # Cache the session snapshot + latest fallback snapshot
            if self.redis and playlist:
                self._set_session_cache(cache_key, playlist)
                self._set_cache(self._get_cache_key(device_id, content_type), playlist)

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
        selected = self._generate_playlist_items(device_id, content_type, size)
        duration_overrides = self._hydrate_duration_overrides(selected)
        return [self._format_item(item, duration_overrides=duration_overrides) for item in selected]

    def _generate_playlist_items(
        self, device_id: str, content_type: ContentType, size: int
    ) -> List[ContentItem]:
        """Generate selected content items (pre-format) for a new playlist."""
        consumed_ids, exposed_ids = self._get_recent_feedback_ids(device_id, content_type)
        negative_item_ids, negative_creator_keys = self._get_recent_negative_feedback(
            device_id, content_type
        )

        # Get candidate items
        candidates = self._get_candidates(content_type)
        if consumed_ids:
            candidates = [item for item in candidates if item.id not in consumed_ids]
        if negative_item_ids or negative_creator_keys:
            candidates = [
                item
                for item in candidates
                if item.id not in negative_item_ids
                and self._creator_feedback_key(item) not in negative_creator_keys
            ]

        if not candidates:
            logger.warning(f"No candidates found for {content_type}")
            return []

        return self._select_fresh_session_items(
            device_id=device_id,
            candidates=candidates,
            size=size,
            exposed_ids=exposed_ids,
        )

    def _relaxed_fill_items(
        self,
        selected_items: List[ContentItem],
        scored_candidates: List[Tuple[ContentItem, float]],
        target_size: int,
    ) -> List[ContentItem]:
        """Fill remaining slots using relaxed constraints to prevent starvation."""
        selected: List[ContentItem] = list(selected_items)
        seen_ids = {item.id for item in selected}
        used_clusters = {item.cluster_id for item in selected if item.cluster_id}

        for item, _score in scored_candidates:
            if len(selected) >= target_size:
                break
            if item.id in seen_ids:
                continue
            if item.cluster_id and item.cluster_id in used_clusters:
                continue

            selected.append(item)
            seen_ids.add(item.id)
            if item.cluster_id:
                used_clusters.add(item.cluster_id)

        return selected

    def _top_up_items_with_historical(
        self,
        *,
        device_id: str,
        content_type: ContentType,
        selected_items: List[ContentItem],
        target_size: int,
    ) -> List[ContentItem]:
        """Top up short playlists from a wider historical window."""
        if len(selected_items) >= target_size:
            return selected_items

        fallback_candidates = self._get_candidates(
            content_type=content_type,
            hours_back=FALLBACK_CONTENT_AGE_HOURS,
        )
        if not fallback_candidates:
            return selected_items

        consumed_ids, exposed_ids = self._get_recent_feedback_ids(device_id, content_type)
        negative_item_ids, negative_creator_keys = self._get_recent_negative_feedback(
            device_id, content_type
        )
        selected_ids = {item.id for item in selected_items}
        return self._select_fresh_session_items(
            device_id=device_id,
            candidates=[
                item
                for item in fallback_candidates
                if item.id not in selected_ids
                and item.id not in consumed_ids
                and item.id not in negative_item_ids
                and self._creator_feedback_key(item) not in negative_creator_keys
            ],
            size=target_size,
            exposed_ids=exposed_ids,
            selected_items=selected_items,
        )

    def _load_fallback_playlist(self, device_id: str, content_type: ContentType) -> List[Dict]:
        """Load fallback playlist from cache or widened historical window."""
        if self.redis:
            cache_key = self._get_cache_key(device_id, content_type)
            cached = self._get_from_cache(cache_key)
            if cached and self._is_cache_compatible(cached, content_type):
                logger.info("Serving cached fallback playlist for %s", content_type.value)
                return cached
            if cached:
                logger.info(
                    "Discarding stale fallback playlist cache for %s; incompatible payload",
                    content_type.value,
                )

        fallback_candidates = self._get_candidates(
            content_type=content_type,
            hours_back=FALLBACK_CONTENT_AGE_HOURS,
        )
        if not fallback_candidates:
            return []

        consumed_ids, exposed_ids = self._get_recent_feedback_ids(device_id, content_type)
        negative_item_ids, negative_creator_keys = self._get_recent_negative_feedback(
            device_id, content_type
        )
        selected = self._select_fresh_session_items(
            device_id=device_id,
            candidates=[
                item
                for item in fallback_candidates
                if item.id not in consumed_ids
                and item.id not in negative_item_ids
                and self._creator_feedback_key(item) not in negative_creator_keys
            ],
            size=MAX_PLAYLIST_SIZE,
            exposed_ids=exposed_ids,
        )
        logger.info("Serving historical fallback playlist for %s", content_type.value)
        duration_overrides = self._hydrate_duration_overrides(selected)
        return [self._format_item(item, duration_overrides=duration_overrides) for item in selected]

    def _get_candidates(
        self,
        content_type: ContentType,
        *,
        hours_back: int = MAX_CONTENT_AGE_HOURS,
    ) -> List[ContentItem]:
        """Get candidate items for playlist."""
        # Get canonical items from clusters (repo already filters for canonical)
        candidates = self.content_repo.get_items_for_playlist(
            content_type=content_type,
            hours_back=hours_back,
            limit=500,
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
        self,
        device_id: str,
        candidates: List[ContentItem],
        *,
        demoted_ids: Optional[Set[int]] = None,
    ) -> List[Tuple[ContentItem, float]]:
        """Score candidates with multi-factor ranking."""
        scored = []
        demoted_ids = demoted_ids or set()

        for item in candidates:
            personalization = self.personalization.compute_personalization_score(device_id, item)
            final_score = self.ranking_service.score_item(
                item, personalization_score=personalization
            )
            if item.id in demoted_ids:
                final_score *= EXPOSED_ONLY_DEMOTION_MULTIPLIER
            scored.append((item, final_score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored

    def _select_fresh_session_items(
        self,
        *,
        device_id: str,
        candidates: List[ContentItem],
        size: int,
        exposed_ids: Set[int],
        selected_items: Optional[List[ContentItem]] = None,
    ) -> List[ContentItem]:
        """
        Fill fresh sessions from unseen items first, using exposed items only
        when the unseen pool is exhausted.
        """
        selected = list(selected_items or [])
        if len(selected) >= size or not candidates:
            return selected

        unseen_candidates = [item for item in candidates if item.id not in exposed_ids]
        unseen_scored = self._score_candidates(device_id, unseen_candidates)
        if not selected:
            selected = self._select_diverse_items(unseen_scored, size)
        selected = self._relaxed_fill_items(selected, unseen_scored, size)

        if len(selected) >= size:
            return selected

        exposed_candidates = [item for item in candidates if item.id in exposed_ids]
        if not exposed_candidates:
            return selected

        exposed_scored = self._score_candidates(
            device_id,
            exposed_candidates,
            demoted_ids=exposed_ids,
        )
        return self._relaxed_fill_items(selected, exposed_scored, size)

    def _get_recent_feedback_ids(
        self, device_id: str, content_type: ContentType
    ) -> Tuple[Set[int], Set[int]]:
        """Return consumed ids and exposed-only ids for fresh-session generation."""
        if not self.interaction_repo:
            return set(), set()

        if content_type == ContentType.ARTICLE:
            consumed_event_types = ARTICLE_CONSUMED_EVENTS
            exposed_event_types: Set[EventType] = set()
        else:
            consumed_event_types = VIDEO_CONSUMED_EVENTS
            exposed_event_types = VIDEO_EXPOSED_EVENTS

        return self.interaction_repo.get_recent_feedback_ids(
            device_id=device_id,
            consumed_event_types=consumed_event_types,
            consumed_hours=CONSUMED_SUPPRESSION_HOURS,
            exposed_event_types=exposed_event_types,
            exposed_hours=EXPOSED_DEMOTION_HOURS,
        )

    def _get_recent_negative_feedback(
        self, device_id: str, content_type: ContentType
    ) -> Tuple[Set[int], Set[str]]:
        """Return recent skipped item ids and creator-downvote keys."""
        if not self.interaction_repo or content_type == ContentType.ARTICLE:
            return set(), set()

        return self.interaction_repo.get_recent_negative_feedback(
            device_id=device_id,
            item_hours=NEGATIVE_ITEM_SUPPRESSION_HOURS,
            creator_hours=NEGATIVE_CREATOR_SUPPRESSION_HOURS,
            content_types=(ContentType.VIDEO, ContentType.REEL),
        )

    def _creator_feedback_key(self, item: ContentItem) -> str:
        """Normalize creator identity for negative-feedback filtering."""
        return (
            str(getattr(item, "channel_id", "") or "").strip().lower()
            or str(getattr(item, "source", "") or "").strip().lower()
            or "unknown"
        )

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

            # Check source cap in rolling window
            if not self._check_window_source_cap(item, selected):
                continue

            # Check category cap in rolling window
            if not self._check_window_category_cap(item, selected):
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

        # Bootstrap small playlists first; strict dominance this early can starve
        # sources where tagging is dense and overlapping.
        if current_size < 5:
            return True

        primary_topic = str(item.topics[0]).lower() if item.topics else ""
        if not primary_topic:
            return True

        current_count = topic_counts.get(primary_topic, 0)
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

    def _check_window_source_cap(self, item: ContentItem, selected: List[ContentItem]) -> bool:
        """Enforce max same-source items in rolling window."""
        source = (item.source or "").lower()
        if not source:
            return True

        lookback = min(len(selected), SOURCE_CAP_WINDOW_SIZE - 1)
        window = selected[-lookback:] if lookback > 0 else []
        source_count = sum(1 for candidate in window if (candidate.source or "").lower() == source)
        return (source_count + 1) <= MAX_SOURCE_PER_WINDOW

    def _check_window_category_cap(self, item: ContentItem, selected: List[ContentItem]) -> bool:
        """Enforce max per-category share in rolling window."""
        topics = item.topics or []
        if not topics:
            return True

        primary = str(topics[0]).lower()
        if not primary:
            return True

        max_per_window = max(1, int(CATEGORY_CAP_WINDOW_SIZE * MAX_CATEGORY_SHARE_PER_WINDOW))
        lookback = min(len(selected), CATEGORY_CAP_WINDOW_SIZE - 1)
        window = selected[-lookback:] if lookback > 0 else []
        category_count = 0
        for candidate in window:
            candidate_topics = candidate.topics or []
            if candidate_topics and str(candidate_topics[0]).lower() == primary:
                category_count += 1
        return (category_count + 1) <= max_per_window

    def _hydrate_duration_overrides(self, items: List[ContentItem]) -> Dict[int, int]:
        if not items:
            return {}
        return hydrate_missing_video_durations(items, content_repo=self.content_repo)

    def _format_item(
        self,
        item: ContentItem,
        *,
        duration_overrides: Optional[Dict[int, int]] = None,
    ) -> Dict:
        """Format content item for API response."""
        item_type = effective_content_type(item)
        duration_seconds = (
            duration_overrides.get(item.id)
            if duration_overrides and item.id in duration_overrides
            else item.duration_seconds
        )
        return {
            "id": item.id,
            "type": item_type.value,
            "source": item.source,
            "source_url": item.source_url,
            "title": item.title,
            "description": item.description,
            "summary": item.summary,
            "image_url": item.image_url,
            "video_url": item.video_url,
            "duration": duration_seconds,
            "topics": item.topics or [],
            "entities": item.entities or [],
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "global_score": item.global_score,
            "cluster_id": item.cluster_id,
            "conversation_starters": item.conversation_starters,
        }

    def _is_cache_compatible(self, playlist: List[Dict], content_type: ContentType) -> bool:
        """Reject cached playlist snapshots whose payload no longer matches serving rules."""
        if not playlist:
            return True

        expected_type = content_type.value
        for item in playlist:
            if "conversation_starters" not in item:
                return False

            item_type = str(item.get("type") or "").upper()
            url = str(item.get("video_url") or item.get("source_url") or "")
            has_shorts_url = has_explicit_shorts_url(url)

            if content_type == ContentType.VIDEO:
                if item_type != ContentType.VIDEO.value or has_shorts_url:
                    return False
            elif content_type == ContentType.REEL:
                if item_type != ContentType.REEL.value:
                    return False
            elif item_type != expected_type:
                return False

        return True

    def _paginate(self, items: List[Dict], offset: int, size: int) -> List[Dict]:
        """Paginate playlist items."""
        start = max(0, offset)
        end = start + size
        return items[start:end]

    def _get_cache_key(self, device_id: str, content_type: ContentType) -> str:
        """Generate cache key for playlist."""
        # Use hash of device_id for privacy
        device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
        return f"{self._cache_prefix(content_type)}{device_hash}:{content_type.value}"

    def _get_session_cache_key(
        self, device_id: str, content_type: ContentType, session_id: str
    ) -> str:
        """Generate cache key for session snapshot."""
        device_hash = hashlib.md5(device_id.encode()).hexdigest()[:12]
        return (
            f"{self._cache_prefix(content_type)}session:"
            f"{device_hash}:{content_type.value}:{session_id}"
        )

    def _cache_prefix(self, content_type: ContentType) -> str:
        """Use a versioned cache namespace for video/reel playlists after surface-rule fix."""
        if content_type in (ContentType.VIDEO, ContentType.REEL):
            return VIDEO_REEL_PLAYLIST_CACHE_PREFIX
        return PLAYLIST_CACHE_PREFIX

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
        try:
            keys = []
            for prefix in (PLAYLIST_CACHE_PREFIX, VIDEO_REEL_PLAYLIST_CACHE_PREFIX):
                keys.extend(self.redis.keys(f"{prefix}{device_hash}:*"))
                keys.extend(self.redis.keys(f"{prefix}session:{device_hash}:*"))
            if keys:
                deduped = list(dict.fromkeys(keys))
                self.redis.delete(*deduped)
                logger.debug(f"Invalidated {len(deduped)} playlist caches for user")
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
        interaction_repo=event_repo,
        redis_client=redis_client,
    )
