"""Article routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import redis

from app.core.dependencies import get_db, get_redis
from app.core.exceptions import ArticleNotFoundError, not_found_exception
from app.core.logging import get_logger
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
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentType

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


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


def _content_item_to_article_schema(item) -> dict:
    """Convert ContentItem to Article schema format."""
    # Extract tags from topics
    tags = [{"name": topic} for topic in (item.topics or [])]
    
    return {
        "id": item.id,
        "title": item.title,
        "source_url": item.source_url,
        "summary": item.summary or "",
        "image_url": item.image_url,
        "published_date": item.published_at.date() if item.published_at else None,
        "created_at": item.created_at,
        "read_time_minutes": max(1, len(item.summary or "") // 200) if item.summary else 1,
        "tags": tags
    }


@router.get("/next", response_model=ArticleSchema)
def get_next_article(
    current_id: Optional[int] = Query(None, description="Current article ID"),
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """
    Get the next article after the current one.
    Returns the most recent article if no current_id is provided.
    Only returns AI-processed articles with valid summaries.
    """
    # Get recent AI-processed articles
    items = content_repo.get_by_type(
        ContentType.ARTICLE,
        limit=50,
        hours_back=168,  # 7 days
        ai_processed_only=True
    )
    
    if not items:
        raise not_found_exception("Article", current_id or "latest")
    
    if current_id is None:
        # Return most recent
        return _content_item_to_article_schema(items[0])
    
    # Find current position and return next
    for i, item in enumerate(items):
        if item.id == current_id:
            if i + 1 < len(items):
                return _content_item_to_article_schema(items[i + 1])
            else:
                # Wrap to first
                return _content_item_to_article_schema(items[0])
    
    # Current not found, return first
    return _content_item_to_article_schema(items[0])


@router.get("/cache", response_model=List[ArticleSchema])
def get_cached_articles(
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """Get pre-cached articles for quick access."""
    items = content_repo.get_by_type(
        ContentType.ARTICLE,
        limit=5,
        hours_back=72,
        ai_processed_only=True
    )
    
    if not items:
        raise HTTPException(status_code=404, detail="No cached articles found")
    
    return [_content_item_to_article_schema(item) for item in items]


@router.get("/recent", response_model=ArticleList)
def get_recent_articles(
    limit: int = Query(5, ge=1, le=50, description="Number of articles to return"),
    page: int = Query(1, ge=1, description="Page number"),
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """
    Get the most recent articles.
    Only returns AI-processed articles with valid summaries.
    """
    offset = (page - 1) * limit
    
    items = content_repo.get_by_type(
        ContentType.ARTICLE,
        limit=limit,
        offset=offset,
        hours_back=168,  # 7 days
        ai_processed_only=True
    )
    
    if not items and page == 1:
        raise HTTPException(status_code=404, detail="No articles found")
    
    articles = [_content_item_to_article_schema(item) for item in items]
    logger.info(f"Returning {len(articles)} AI-processed articles (page {page})")
    
    return {"articles": articles}


@router.get("/tags/{tag_name}", response_model=ArticleList)
def get_articles_by_tag(
    tag_name: str,
    limit: int = Query(10, ge=1, le=50, description="Number of articles to return"),
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """Get articles by tag/topic name."""
    # Get all recent articles and filter by topic
    items = content_repo.get_by_type(
        ContentType.ARTICLE,
        limit=200,
        hours_back=168,
        ai_processed_only=True
    )
    
    # Filter by topic
    matching = [
        item for item in items
        if tag_name.lower() in [t.lower() for t in (item.topics or [])]
    ][:limit]
    
    if not matching:
        raise HTTPException(status_code=404, detail=f"No articles found with tag '{tag_name}'")
    
    return {"articles": [_content_item_to_article_schema(item) for item in matching]}


@router.get("/tags", response_model=List[TagCount])
def get_popular_tags(
    limit: int = Query(10, ge=1, le=50, description="Number of tags to return"),
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """Get the most popular tags with article counts."""
    distribution = content_repo.get_topic_distribution(
        ContentType.ARTICLE,
        hours_back=168
    )
    
    return [{"name": topic, "count": count} for topic, count in distribution[:limit]]


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
    content_repo: ContentItemRepository = Depends(get_content_repo),
    article_service: ArticleService = Depends(get_article_service)
):
    """Get a specific article by ID, including conversation history."""
    # Try content_items first
    item = content_repo.get_by_id(article_id)
    if item and item.type == ContentType.ARTICLE:
        article_data = _content_item_to_article_schema(item)
        article_data["conversations"] = []
        return article_data
    
    # Fallback to legacy articles table
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
