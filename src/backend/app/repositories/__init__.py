"""Repository layer for database operations.

Repositories encapsulate database queries and provide a clean interface
for data access. They abstract away SQLAlchemy specifics from services.
"""

from app.repositories.base import BaseRepository

__all__ = [
    "BaseRepository",
    "ConversationRepository",
    "UsageRepository",
    "ContentItemRepository",
    "UserProfileRepository",
    "UserPreferenceRepository",
    "InteractionEventRepository",
]


def __getattr__(name):
    """Lazily resolve repository imports to avoid package cycles."""
    if name == "ContentItemRepository":
        from app.repositories.content_repo import ContentItemRepository

        return ContentItemRepository
    if name == "ConversationRepository":
        from app.repositories.conversation_repo import ConversationRepository

        return ConversationRepository
    if name == "UsageRepository":
        from app.repositories.usage_repo import UsageRepository

        return UsageRepository
    if name == "UserProfileRepository":
        from app.repositories.user_repo import UserProfileRepository

        return UserProfileRepository
    if name == "UserPreferenceRepository":
        from app.repositories.user_repo import UserPreferenceRepository

        return UserPreferenceRepository
    if name == "InteractionEventRepository":
        from app.repositories.user_repo import InteractionEventRepository

        return InteractionEventRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
