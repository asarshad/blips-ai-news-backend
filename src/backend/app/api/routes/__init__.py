"""
API Routes - HTTP endpoint handlers.

All routes use dependency injection for services and repositories.
Exceptions are handled by the exception handlers in the API layer.
"""

from app.api.routes.ai_chat import router as ai_chat_router
from app.api.routes.articles import router as articles_router
from app.api.routes.conversation import router as conversation_router
from app.api.routes.session import router as session_router
from app.api.routes.usage import router as usage_router
from app.api.routes.videos import router as videos_router

__all__ = [
    "articles_router",
    "videos_router",
    "ai_chat_router",
    "conversation_router",
    "usage_router",
    "session_router",
]
