"""
Repository layer for database operations.

Repositories encapsulate database queries and provide a clean interface
for data access. They abstract away SQLAlchemy specifics from services.
"""

from app.repositories.base import BaseRepository
from app.repositories.article_repo import ArticleRepository
from app.repositories.video_repo import VideoRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.usage_repo import UsageRepository
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import (
    UserProfileRepository,
    UserPreferenceRepository,
    InteractionEventRepository
)

__all__ = [
    "BaseRepository",
    "ArticleRepository",
    "VideoRepository",
    "ConversationRepository",
    "UsageRepository",
    "ContentItemRepository",
    "UserProfileRepository",
    "UserPreferenceRepository",
    "InteractionEventRepository",
]
