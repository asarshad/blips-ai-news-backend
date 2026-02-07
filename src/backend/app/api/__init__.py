
from fastapi import APIRouter

from app.api.routes import admin, ai_chat, articles, conversation, inventory, quality, session, starters, usage, videos

api_router = APIRouter()

api_router.include_router(articles.router, prefix="/articles", tags=["articles"])
api_router.include_router(ai_chat.router, prefix="/ai", tags=["ai"])
api_router.include_router(conversation.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(starters.router, prefix="/starters", tags=["starters"])
api_router.include_router(usage.router, prefix="/usage", tags=["usage"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(quality.router, prefix="/quality", tags=["quality"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])

