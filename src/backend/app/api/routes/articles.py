
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
import redis

from app.db.base import get_db
from app.models.article import Article
from app.schemas.article import Article as ArticleSchema, ArticleWithConversation
from app.services.article_service import ArticleService
from app.config import settings

# Redis connection
redis_client = redis.from_url(settings.REDIS_URL)

router = APIRouter()

@router.get("/next", response_model=ArticleSchema)
def get_next_article(
    current_id: int = Query(None, description="Current article ID"),
    db: Session = Depends(get_db)
):
    article_service = ArticleService(db, redis_client)
    article = article_service.get_next_article(current_id)
    
    if not article:
        raise HTTPException(status_code=404, detail="No articles found")
    
    return article

@router.get("/{article_id}", response_model=ArticleWithConversation)
def get_article(
    article_id: int,
    db: Session = Depends(get_db)
):
    article_service = ArticleService(db, redis_client)
    article = article_service.get_article_by_id(article_id)
    
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    return article

@router.get("/cache", response_model=List[ArticleSchema])
def get_cached_articles(
    db: Session = Depends(get_db)
):
    article_service = ArticleService(db, redis_client)
    cached_articles = article_service.get_cached_articles()
    
    if not cached_articles:
        raise HTTPException(status_code=404, detail="No cached articles found")
    
    return cached_articles
