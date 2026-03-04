"""
Repository for user profiles and preferences.

Handles user profile management and preference learning.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.content import (
    EventType,
    InteractionEvent,
    PrefType,
    UserCategorySelection,
    UserPreference,
    UserProfile,
)
from app.repositories.base import BaseRepository


class UserProfileRepository(BaseRepository[UserProfile]):
    """Repository for UserProfile operations."""

    def __init__(self, db: Session):
        super().__init__(db, UserProfile)

    def get_by_device_id(self, device_id: str) -> Optional[UserProfile]:
        """Get user profile by device ID."""
        return self.db.query(UserProfile).filter(UserProfile.device_id == device_id).first()

    def get_or_create(self, device_id: str) -> UserProfile:
        """Get existing profile or create new one."""
        profile = self.get_by_device_id(device_id)
        if not profile:
            profile = UserProfile(device_id=device_id)
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
        return profile

    def update_timestamp(self, device_id: str) -> bool:
        """Update the profile's updated_at timestamp."""
        result = (
            self.db.query(UserProfile)
            .filter(UserProfile.device_id == device_id)
            .update({UserProfile.updated_at: datetime.utcnow()})
        )
        self.db.commit()
        return result > 0


class UserPreferenceRepository(BaseRepository[UserPreference]):
    """Repository for UserPreference operations."""

    def __init__(self, db: Session):
        super().__init__(db, UserPreference)

    def get_preferences(
        self, device_id: str, pref_type: Optional[PrefType] = None, min_weight: float = 0.0
    ) -> List[UserPreference]:
        """
        Get user preferences, optionally filtered by type.

        Args:
            device_id: User's device ID
            pref_type: Optional filter by preference type
            min_weight: Minimum weight threshold

        Returns:
            List of preferences sorted by weight descending
        """
        query = self.db.query(UserPreference).filter(
            UserPreference.device_id == device_id, UserPreference.weight >= min_weight
        )

        if pref_type:
            query = query.filter(UserPreference.pref_type == pref_type)

        return query.order_by(desc(UserPreference.weight)).all()

    def get_preference(
        self, device_id: str, pref_type: PrefType, key: str
    ) -> Optional[UserPreference]:
        """Get a specific preference."""
        return (
            self.db.query(UserPreference)
            .filter(
                UserPreference.device_id == device_id,
                UserPreference.pref_type == pref_type,
                UserPreference.key == key,
            )
            .first()
        )

    def upsert_preference(
        self, device_id: str, pref_type: PrefType, key: str, weight_delta: float
    ) -> UserPreference:
        """
        Update preference weight or create if not exists.

        Args:
            device_id: User's device ID
            pref_type: Type of preference
            key: Preference key (topic, entity, source, format)
            weight_delta: Amount to add to current weight

        Returns:
            Updated or created preference
        """
        pref = self.get_preference(device_id, pref_type, key)

        if pref:
            pref.weight += weight_delta
            pref.updated_at = datetime.utcnow()
        else:
            pref = UserPreference(
                device_id=device_id, pref_type=pref_type, key=key, weight=weight_delta
            )
            self.db.add(pref)

        self.db.commit()
        self.db.refresh(pref)
        return pref

    def apply_decay(self, decay_rate: float = 0.02, min_weight: float = 0.1) -> int:
        """
        Apply daily decay to all preference weights.

        Args:
            decay_rate: Percentage to decay (0.02 = 2%)
            min_weight: Delete preferences below this weight

        Returns:
            Number of preferences updated
        """
        # Apply decay
        decay_factor = 1 - decay_rate
        result = self.db.query(UserPreference).update(
            {
                UserPreference.weight: UserPreference.weight * decay_factor,
                UserPreference.updated_at: datetime.utcnow(),
            }
        )
        self.db.commit()

        # Delete low-weight preferences
        self.db.query(UserPreference).filter(UserPreference.weight < min_weight).delete()
        self.db.commit()

        return result

    def get_top_preferences(
        self, device_id: str, limit: int = 10
    ) -> Dict[PrefType, List[UserPreference]]:
        """Get top preferences grouped by type."""
        result = {}
        for pref_type in PrefType:
            prefs = (
                self.db.query(UserPreference)
                .filter(
                    UserPreference.device_id == device_id,
                    UserPreference.pref_type == pref_type,
                    UserPreference.weight > 0,
                )
                .order_by(desc(UserPreference.weight))
                .limit(limit)
                .all()
            )
            result[pref_type] = prefs
        return result


class InteractionEventRepository(BaseRepository[InteractionEvent]):
    """Repository for InteractionEvent operations (append-only)."""

    def __init__(self, db: Session):
        super().__init__(db, InteractionEvent)

    def record_event(
        self,
        device_id: str,
        content_item_id: int,
        event_type: EventType,
        event_value: Optional[str] = None,
    ) -> InteractionEvent:
        """
        Record a new interaction event.

        Args:
            device_id: User's device ID
            content_item_id: ID of the content item
            event_type: Type of interaction
            event_value: Optional additional data

        Returns:
            Created event
        """
        event = InteractionEvent(
            device_id=device_id,
            content_item_id=content_item_id,
            event_type=event_type,
            event_value=event_value,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_user_events(
        self, device_id: str, hours_back: int = 24, event_types: Optional[List[EventType]] = None
    ) -> List[InteractionEvent]:
        """Get recent events for a user."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        query = self.db.query(InteractionEvent).filter(
            InteractionEvent.device_id == device_id, InteractionEvent.created_at >= cutoff
        )

        if event_types:
            query = query.filter(InteractionEvent.event_type.in_(event_types))

        return query.order_by(desc(InteractionEvent.created_at)).all()

    def get_content_events(
        self, content_item_id: int, hours_back: int = 24
    ) -> List[InteractionEvent]:
        """Get recent events for a content item."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        return (
            self.db.query(InteractionEvent)
            .filter(
                InteractionEvent.content_item_id == content_item_id,
                InteractionEvent.created_at >= cutoff,
            )
            .order_by(desc(InteractionEvent.created_at))
            .all()
        )

    def count_events_by_type(
        self, content_item_id: int, hours_back: int = 24
    ) -> Dict[EventType, int]:
        """Get event counts by type for a content item."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        results = (
            self.db.query(InteractionEvent.event_type, func.count(InteractionEvent.id))
            .filter(
                InteractionEvent.content_item_id == content_item_id,
                InteractionEvent.created_at >= cutoff,
            )
            .group_by(InteractionEvent.event_type)
            .all()
        )

        return {event_type: count for event_type, count in results}

    def count_user_chat_messages_today(self, device_id: str, content_item_id: int) -> int:
        """Count chat messages from user on content item today (for capping)."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        return (
            self.db.query(InteractionEvent)
            .filter(
                InteractionEvent.device_id == device_id,
                InteractionEvent.content_item_id == content_item_id,
                InteractionEvent.event_type == EventType.CHAT_MESSAGE,
                InteractionEvent.created_at >= today_start,
            )
            .count()
        )

    def get_interacted_content_ids(self, device_id: str, hours_back: int = 72) -> List[int]:
        """Get IDs of content the user has interacted with."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        results = (
            self.db.query(InteractionEvent.content_item_id)
            .filter(InteractionEvent.device_id == device_id, InteractionEvent.created_at >= cutoff)
            .distinct()
            .all()
        )

        return [r[0] for r in results]

    def get_engagement_stats(self, content_item_id: int, hours_back: int = 48) -> Dict[str, int]:
        """Get engagement statistics for a content item."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # Count unique users
        unique_users = (
            self.db.query(func.count(func.distinct(InteractionEvent.device_id)))
            .filter(
                InteractionEvent.content_item_id == content_item_id,
                InteractionEvent.created_at >= cutoff,
            )
            .scalar()
        )

        # Count total events
        total_events = (
            self.db.query(InteractionEvent)
            .filter(
                InteractionEvent.content_item_id == content_item_id,
                InteractionEvent.created_at >= cutoff,
            )
            .count()
        )

        # Get event breakdown
        event_counts = self.count_events_by_type(content_item_id, hours_back)

        return {
            "unique_users": unique_users or 0,
            "total_events": total_events,
            "views": event_counts.get(EventType.VIEW_10S, 0),
            "opens": event_counts.get(EventType.OPEN_SOURCE, 0),
            "shares": event_counts.get(EventType.SHARE, 0),
            "saves": event_counts.get(EventType.SAVE, 0),
            "chat_starts": event_counts.get(EventType.CHAT_START, 0),
            "chat_messages": event_counts.get(EventType.CHAT_MESSAGE, 0),
        }


class UserCategorySelectionRepository(BaseRepository[UserCategorySelection]):
    """Repository for explicit user-declared category interests."""

    def __init__(self, db: Session):
        super().__init__(db, UserCategorySelection)

    def get_by_device_id(self, device_id: str) -> Optional[UserCategorySelection]:
        """Return the category selection row for a device, or None."""
        return (
            self.db.query(UserCategorySelection)
            .filter(UserCategorySelection.device_id == device_id)
            .first()
        )

    def get_selected_categories(self, device_id: str) -> List[str]:
        """
        Return the list of selected category strings for a device.

        Returns an empty list if the user has not set any preferences yet.
        """
        row = self.get_by_device_id(device_id)
        if row is None:
            return []
        return row.selected_categories or []

    def upsert(self, device_id: str, categories: List[str]) -> UserCategorySelection:
        """
        Create or replace the category selection for a device.

        Args:
            device_id: User device identifier.
            categories: Ordered list of category strings to store.

        Returns:
            The persisted UserCategorySelection row.
        """
        # Ensure parent profile exists
        profile_repo = UserProfileRepository(self.db)
        profile_repo.get_or_create(device_id)

        row = self.get_by_device_id(device_id)
        if row is None:
            row = UserCategorySelection(
                device_id=device_id,
                selected_categories=categories,
            )
            self.db.add(row)
        else:
            row.selected_categories = categories
            row.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(row)
        return row

    def get_total_learned_weight(self, device_id: str) -> float:
        """
        Return the sum of all UserPreference weights for a device.

        Used to derive the engagement_decay_factor for the personalised
        feed score — as the user interacts more, engagement overrides
        their declared categories.
        """
        result = (
            self.db.query(func.sum(UserPreference.weight))
            .filter(UserPreference.device_id == device_id)
            .scalar()
        )
        return float(result or 0.0)
