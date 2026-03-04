from fastapi import APIRouter

from app.api.admin import admin_router as editorial_admin_router
from app.api.admin import admin_ui_router
from app.api.routes import admin as admin_routes
from app.api.routes import (
    ai_chat,
    articles,
    config,
    conversation,
    debug,
    events,
    inventory,
    metrics,
    preferences,
    quality,
    session,
    starters,
    usage,
    videos,
)
from app.core.config import settings

api_router = APIRouter()

api_router.include_router(articles.router, prefix="/articles", tags=["articles"])
api_router.include_router(config.router, tags=["config"])
api_router.include_router(events.router, tags=["events"])
api_router.include_router(ai_chat.router, prefix="/ai", tags=["ai"])
api_router.include_router(conversation.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(starters.router, prefix="/starters", tags=["starters"])
api_router.include_router(usage.router, prefix="/usage", tags=["usage"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(preferences.router, prefix="/users", tags=["preferences"])
api_router.include_router(admin_routes.router, prefix="/admin", tags=["admin"])
api_router.include_router(editorial_admin_router, tags=["admin-editorial"])
api_router.include_router(admin_ui_router, tags=["admin-ui"])
api_router.include_router(quality.router, prefix="/quality", tags=["quality"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])
api_router.include_router(metrics.router, tags=["metrics"])

if settings.DEBUG_ROUTES_ENABLED:
    api_router.include_router(debug.router, prefix="/debug", tags=["debug"])
