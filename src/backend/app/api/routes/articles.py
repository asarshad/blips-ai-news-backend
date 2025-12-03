
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import redis

from app.db.base import get_db
from app.models.article import Article
from app.schemas.article import Article as ArticleSchema, ArticleWithConversation, ArticleList, TagCount
from app.services.article_service import ArticleService
from app.core.config import settings

# Redis connection
redis_client = redis.from_url(settings.REDIS_URL)

router = APIRouter()

@router.get("/next", response_model=ArticleSchema)
def get_next_article(
    current_id: Optional[int] = Query(None, description="Current article ID"),
    db: Session = Depends(get_db)
):
    """
    Get the next article after the current one, or the most recent if no current_id is provided.
    This enables the swipe-through reading experience.
    """
    article_service = ArticleService(db, redis_client)
    article = article_service.get_next_article(current_id)
    
    if not article:
        raise HTTPException(status_code=404, detail="No articles found")
    
    return article

@router.get("/cache", response_model=List[ArticleSchema])
def get_cached_articles(
    db: Session = Depends(get_db)
):
    """
    Get the pre-cached articles for quick access.
    Used for the article carousel UI component.
    """
    article_service = ArticleService(db, redis_client)
    cached_articles = article_service.get_cached_articles()
    
    if not cached_articles:
        raise HTTPException(status_code=404, detail="No cached articles found")
    
    return cached_articles

@router.get("/recent", response_model=ArticleList)
def get_recent_articles(
    limit: int = Query(5, description="Number of articles to return"),
    db: Session = Depends(get_db)
):
    """
    Get the most recent articles.
    Used for the news feed UI component.
    """
    article_service = ArticleService(db, redis_client)
    articles = article_service.get_recent_articles(limit)
    
    if not articles:
        raise HTTPException(status_code=404, detail="No articles found")
    
    return {"articles": articles}

@router.get("/tags/{tag_name}", response_model=ArticleList)
def get_articles_by_tag(
    tag_name: str,
    limit: int = Query(10, description="Number of articles to return"),
    db: Session = Depends(get_db)
):
    """
    Get articles by tag.
    Used for the tag filter UI component.
    """
    article_service = ArticleService(db, redis_client)
    articles = article_service.get_articles_by_tag(tag_name, limit)
    
    if not articles:
        raise HTTPException(status_code=404, detail="No articles found with this tag")
    
    return {"articles": articles}

@router.get("/tags", response_model=List[TagCount])
def get_popular_tags(
    limit: int = Query(10, description="Number of tags to return"),
    db: Session = Depends(get_db)
):
    """
    Get the most popular tags with article counts.
    Used for the tag cloud UI component.
    """
    article_service = ArticleService(db, redis_client)
    tags = article_service.get_popular_tags(limit)
    
    return tags


@router.post("/fetch", response_model=dict)
def fetch_articles(db: Session = Depends(get_db)):
    """
    Manually trigger fetching of new articles from RSS feeds.
    """
    from app.services.news_fetcher import NewsFetcher
    from app.services.summarizer import ArticleSummarizer
    
    try:
        news_fetcher = NewsFetcher(db)
        articles = news_fetcher.fetch_latest_articles()
        
        if not articles:
            return {"message": "No new articles found", "count": 0}
        
        summarizer = ArticleSummarizer(db)
        saved_count = summarizer.process_articles(articles)
        
        return {"message": f"Fetched and processed {saved_count} articles", "count": saved_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/regenerate-summaries", response_model=dict)
def regenerate_summaries(
    limit: int = Query(10, description="Number of articles to process"),
    db: Session = Depends(get_db)
):
    """
    Regenerate summaries for articles that have placeholder summaries.
    """
    from app.services.summarizer import ArticleSummarizer
    
    try:
        summarizer = ArticleSummarizer(db)
        updated_count = summarizer.regenerate_summaries(limit)
        
        return {"message": f"Regenerated summaries for {updated_count} articles", "count": updated_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{article_id}", response_model=ArticleWithConversation)
def get_article(
    article_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a specific article by ID, including its conversation history.
    """
    article_service = ArticleService(db, redis_client)
    article = article_service.get_article_by_id(article_id)
    
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    return article
