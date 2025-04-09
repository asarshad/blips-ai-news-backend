
from fastapi import APIRouter
from app.api.routes import articles, ai_chat, conversation, usage

api_router = APIRouter()

api_router.include_router(articles.router, prefix="/articles", tags=["articles"])
api_router.include_router(ai_chat.router, prefix="/ai", tags=["ai"])
api_router.include_router(conversation.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(usage.router, prefix="/usage", tags=["usage"])
