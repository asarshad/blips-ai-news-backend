"""Article routes for the REST API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.core.exceptions import ArticleNotFoundError, not_found_exception
from app.schemas.article import (
    Article as ArticleSchema, 
    ArticleWithConversation, 
    ArticleList, 
    TagCount
)
from app.services.article_service import ArticleService
from app.services.news_fetcher import NewsFetcher
from app.services.summarizer import ArticleSummarizer
from app.repositories.article_repo import ArticleRepository

router = APIRouter()


def get_article_service(
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
) -> ArticleService:
    """Factory for ArticleService with dependencies."""
    article_repo = ArticleRepository(db)
    return ArticleService(article_repo, redis_client)


def get_article_repo(db: Session = Depends(get_db)) -> ArticleRepository:
    """Factory for ArticleRepository."""
    return ArticleRepository(db)


@router.get("/next", response_model=ArticleSchema)
def get_next_article(
    current_id: Optional[int] = Query(None, description="Current article ID"),
    article_service: ArticleService = Depends(get_article_service)
):
    """
    Get the next article after the current one.
    Returns the most recent article if no current_id is provided.
    """
    article = article_service.get_next_article(current_id)
    if not article:
        raise not_found_exception("Article", current_id or "latest")
    return article


@router.get("/cache", response_model=List[ArticleSchema])
def get_cached_articles(
    article_service: ArticleService = Depends(get_article_service)
):
    """Get pre-cached articles for quick access."""
    cached_articles = article_service.get_cached_articles()
    if not cached_articles:
        raise HTTPException(status_code=404, detail="No cached articles found")
    return cached_articles


@router.get("/recent", response_model=ArticleList)
def get_recent_articles(
    limit: int = Query(5, ge=1, le=50, description="Number of articles to return"),
    article_service: ArticleService = Depends(get_article_service)
):
    """Get the most recent articles."""
    articles = article_service.get_recent_articles(limit)
    if not articles:
        raise HTTPException(status_code=404, detail="No articles found")
    return {"articles": articles}


@router.get("/tags/{tag_name}", response_model=ArticleList)
def get_articles_by_tag(
    tag_name: str,
    limit: int = Query(10, ge=1, le=50, description="Number of articles to return"),
    article_service: ArticleService = Depends(get_article_service)
):
    """Get articles by tag name."""
    articles = article_service.get_articles_by_tag(tag_name, limit)
    if not articles:
        raise HTTPException(status_code=404, detail=f"No articles found with tag '{tag_name}'")
    return {"articles": articles}


@router.get("/tags", response_model=List[TagCount])
def get_popular_tags(
    limit: int = Query(10, ge=1, le=50, description="Number of tags to return"),
    article_service: ArticleService = Depends(get_article_service)
):
    """Get the most popular tags with article counts."""
    return article_service.get_popular_tags(limit)


@router.post("/fetch", response_model=dict)
def fetch_articles(article_repo: ArticleRepository = Depends(get_article_repo)):
    """Manually trigger fetching of new articles from RSS feeds."""
    news_fetcher = NewsFetcher(article_repo)
    articles = news_fetcher.fetch_latest_articles()
    
    if not articles:
        return {"message": "No new articles found", "count": 0}
    
    summarizer = ArticleSummarizer(article_repo)
    saved_count = 0
    
    for article_data in articles:
        try:
            processed = summarizer.summarize_article(article_data)
            summarizer.save_article(processed)
            saved_count += 1
        except Exception:
            continue
    
    return {"message": f"Fetched and processed {saved_count} articles", "count": saved_count}


@router.post("/regenerate-summaries", response_model=dict)
def regenerate_summaries(
    limit: int = Query(10, ge=1, le=100, description="Number of articles to process"),
    article_repo: ArticleRepository = Depends(get_article_repo)
):
    """Regenerate summaries for articles that have placeholder summaries."""
    summarizer = ArticleSummarizer(article_repo)
    updated_count = summarizer.regenerate_summaries(limit)
    return {"message": f"Regenerated summaries for {updated_count} articles", "count": updated_count}


@router.get("/{article_id}", response_model=ArticleWithConversation)
def get_article(
    article_id: int,
    article_service: ArticleService = Depends(get_article_service)
):
    """Get a specific article by ID, including conversation history."""
    try:
        return article_service.get_article_by_id(article_id)
    except ArticleNotFoundError:
        raise not_found_exception("Article", article_id)


# Action weights for hot score
ACTION_WEIGHTS = {
    "open": 1,
    "dwell": 2,
    "share": 3
}


@router.post("/{article_id}/engage", response_model=dict)
def engage_article(
    article_id: int,
    action: str = Query(..., description="Action type: open, dwell, or share"),
    article_repo: ArticleRepository = Depends(get_article_repo)
):
    """
    Record an engagement action on an article.
    Increments hot_score by action weight.
    Actions: open (1), dwell (2), share (3)
    """
    if action not in ACTION_WEIGHTS:
        raise HTTPException(status_code=400, detail=f"Invalid action. Must be one of: {list(ACTION_WEIGHTS.keys())}")
    
    article = article_repo.get_by_id(article_id)
    if not article:
        raise not_found_exception("Article", article_id)
    
    weight = ACTION_WEIGHTS[action]
    article_repo.increment_hot_score(article_id, weight)
    
    return {"success": True, "action": action, "weight": weight}
