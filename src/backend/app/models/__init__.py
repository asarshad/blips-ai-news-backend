
from app.models.article import Article, Tag
from app.models.conversation import Conversation
from app.models.usage import Usage
from app.models.video import Video
from app.models.content import (
    ContentItem, ContentType,
    UserProfile, UserPreference, PrefType,
    InteractionEvent, EventType
)

# For Alembic to detect all models
__all__ = [
    "Article", "Tag", "Conversation", "Usage", "Video",
    "ContentItem", "ContentType",
    "UserProfile", "UserPreference", "PrefType",
    "InteractionEvent", "EventType"
]
