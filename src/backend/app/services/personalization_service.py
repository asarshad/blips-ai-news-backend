"""
Personalization service for user preference learning.

Updates user preferences based on interaction signals:
- VIEW_10S: +1 to topic/entity/source preferences
- OPEN_SOURCE: +3 (strong interest signal)
- SHARE/SAVE: +5 (very strong interest)
- CHAT_START: +6 (engagement intent)
- CHAT_MESSAGE: +1 (capped at 10 per content)

Applies daily decay to prevent stale preferences.
"""

from typing import Dict, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.content import (
    ContentItem,
    EventType,
    InteractionEvent,
    PrefType,
    UserProfile,
)
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import (
    InteractionEventRepository,
    UserPreferenceRepository,
    UserProfileRepository,
)

logger = get_logger(__name__)
settings = get_settings()


# Event weights for preference learning
EVENT_WEIGHTS = {
    EventType.VIEW_10S: 1,
    EventType.OPEN_SOURCE: 3,
    EventType.SHARE: 5,
    EventType.SAVE: 5,
    EventType.CHAT_START: 6,
    EventType.CHAT_MESSAGE: 1,
}

# Chat message cap per content item per day
CHAT_MESSAGE_CAP_PER_CONTENT = 10

# Daily decay factor (preferences reduce by this factor daily)
DAILY_DECAY_FACTOR = 0.95

# Minimum weight before preference is pruned
MIN_PREFERENCE_WEIGHT = 0.1


class PersonalizationService:
    """
    Service for learning and applying user preferences.

    Tracks user interactions and updates topic/entity/source preferences.
    Applies time decay to keep preferences fresh.
    """

    def __init__(
        self,
        profile_repo: UserProfileRepository,
        preference_repo: UserPreferenceRepository,
        event_repo: InteractionEventRepository,
        content_repo: ContentItemRepository,
    ):
        self.profile_repo = profile_repo
        self.preference_repo = preference_repo
        self.event_repo = event_repo
        self.content_repo = content_repo

    def record_interaction(
        self,
        device_id: str,
        content_item_id: int,
        event_type: EventType,
        extra_data: Optional[Dict] = None,
    ) -> Optional[InteractionEvent]:
        """
        Record a user interaction and update preferences.

        Args:
            device_id: User device ID
            content_item_id: ID of content interacted with
            event_type: Type of interaction
            extra_data: Optional additional data

        Returns:
            The created interaction event
        """
        # Get or create user profile
        profile = self.profile_repo.get_or_create(device_id)

        # Get content item
        content = self.content_repo.get_by_id(content_item_id)
        if not content:
            logger.warning(f"Content item {content_item_id} not found")
            return None

        # Check chat message cap
        if event_type == EventType.CHAT_MESSAGE:
            today_count = self.event_repo.count_user_chat_messages_today(
                profile.device_id,
                content_item_id,
            )
            if today_count >= CHAT_MESSAGE_CAP_PER_CONTENT:
                logger.debug(
                    f"Chat message cap reached for user {device_id} on content {content_item_id}"
                )
                # Still record the event, but don't update preferences
                event = self.event_repo.record_event(
                    device_id=profile.device_id,
                    content_item_id=content_item_id,
                    event_type=event_type,
                    event_value=str(extra_data) if extra_data else None,
                )
                return event

        # Record the event
        event = self.event_repo.record_event(
            device_id=profile.device_id,
            content_item_id=content_item_id,
            event_type=event_type,
            event_value=str(extra_data) if extra_data else None,
        )

        if not event:
            return None

        # Update preferences based on this interaction
        self._update_preferences_for_interaction(profile, content, event_type)

        return event

    def _update_preferences_for_interaction(
        self, profile: UserProfile, content: ContentItem, event_type: EventType
    ):
        """Update user preferences based on an interaction."""
        weight = EVENT_WEIGHTS.get(event_type, 1)

        # Update topic preferences
        if content.topics:
            for topic in content.topics:
                self.preference_repo.upsert_preference(
                    device_id=profile.device_id,
                    pref_type=PrefType.TOPIC,
                    key=topic.lower(),
                    weight_delta=weight,
                )

        # Update entity preferences
        if content.entities:
            for entity in content.entities:
                self.preference_repo.upsert_preference(
                    device_id=profile.device_id,
                    pref_type=PrefType.ENTITY,
                    key=str(entity).lower(),
                    weight_delta=weight,
                )

        # Update source preference
        if content.source:
            self.preference_repo.upsert_preference(
                device_id=profile.device_id,
                pref_type=PrefType.SOURCE,
                key=content.source.lower(),
                weight_delta=weight,
            )

        # Update format preference
        self.preference_repo.upsert_preference(
            device_id=profile.device_id,
            pref_type=PrefType.FORMAT,
            key=content.type.value,
            weight_delta=weight,
        )

    def run_decay_job(self) -> Dict[str, int]:
        """
        Run daily preference decay for all users.

        Returns:
            Statistics about the decay run
        """
        stats = {
            "users_processed": 0,
            "preferences_decayed": 0,
            "preferences_pruned": 0,
            "errors": 0,
        }

        profiles = self.profile_repo.get_all(limit=10000)
        logger.info(f"Running decay for {len(profiles)} users")

        try:
            decayed = self.preference_repo.apply_decay(
                decay_rate=1 - DAILY_DECAY_FACTOR,
                min_weight=MIN_PREFERENCE_WEIGHT,
            )
            stats["users_processed"] = len(profiles)
            stats["preferences_decayed"] = decayed
        except Exception as e:
            logger.error(f"Error decaying preferences: {e}")
            stats["errors"] += 1

        logger.info(f"Decay complete: {stats}")
        return stats

    def compute_personalization_score(self, device_id: str, content: ContentItem) -> float:
        """
        Compute how well content matches user preferences.

        Returns:
            Score from 0.0 to 1.0 indicating preference match
        """
        profile = self.profile_repo.get_by_device_id(device_id)
        if not profile:
            return 0.0

        # Get user's preferences
        topic_prefs = {
            p.key: p.weight
            for p in self.preference_repo.get_top_preferences(profile.device_id, limit=50).get(
                PrefType.TOPIC, []
            )
        }
        entity_prefs = {
            p.key: p.weight
            for p in self.preference_repo.get_top_preferences(profile.device_id, limit=50).get(
                PrefType.ENTITY, []
            )
        }
        source_prefs = {
            p.key: p.weight
            for p in self.preference_repo.get_top_preferences(profile.device_id, limit=50).get(
                PrefType.SOURCE, []
            )
        }
        format_prefs = {
            p.key: p.weight
            for p in self.preference_repo.get_top_preferences(profile.device_id, limit=10).get(
                PrefType.FORMAT, []
            )
        }

        # Compute topic match
        topic_score = 0.0
        if content.topics and topic_prefs:
            topic_weights = [topic_prefs.get(t.lower(), 0) for t in content.topics]
            if topic_weights:
                topic_score = sum(topic_weights) / max(topic_prefs.values())

        # Compute entity match
        entity_score = 0.0
        if content.entities and entity_prefs:
            entity_weights = [entity_prefs.get(e.lower(), 0) for e in content.entities]
            if entity_weights:
                entity_score = sum(entity_weights) / max(entity_prefs.values())

        # Compute source match
        source_score = 0.0
        if content.source and source_prefs:
            source_weight = source_prefs.get(content.source.lower(), 0)
            if source_prefs:
                source_score = source_weight / max(source_prefs.values())

        # Compute format match
        format_score = 0.0
        if format_prefs:
            format_weight = format_prefs.get(content.type.value, 0)
            if format_prefs:
                format_score = format_weight / max(format_prefs.values())

        # Weighted combination using configurable weights
        # Topics and entities matter most, then source, then format
        personalization = (
            settings.PERSONALIZATION_TOPIC_WEIGHT * min(1.0, topic_score)
            + settings.PERSONALIZATION_ENTITY_WEIGHT * min(1.0, entity_score)
            + settings.PERSONALIZATION_SOURCE_WEIGHT * min(1.0, source_score)
            + settings.PERSONALIZATION_FORMAT_WEIGHT * min(1.0, format_score)
        )

        return min(1.0, max(0.0, personalization))
