from app.models.candidate_audit import CandidateAuditEvent
from app.models.content import (
    ContentItem,
    ContentType,
    EventType,
    InteractionEvent,
    PrefType,
    UserPreference,
    UserProfile,
)
from app.models.conversation import Conversation
from app.models.editorial import EditorialAction
from app.models.ingestion_progress import IngestionProgress
from app.models.push import PushSendLog, PushSubscription
from app.models.usage import Usage
from app.models.video_source import VideoDiscoveryRun, VideoSourceProfile

# For Alembic to detect all models
__all__ = [
    "Conversation",
    "Usage",
    "CandidateAuditEvent",
    "ContentItem",
    "ContentType",
    "UserProfile",
    "UserPreference",
    "PrefType",
    "InteractionEvent",
    "EventType",
    "PushSubscription",
    "PushSendLog",
    "IngestionProgress",
    "EditorialAction",
    "VideoSourceProfile",
    "VideoDiscoveryRun",
]
