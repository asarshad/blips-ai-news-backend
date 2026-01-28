"""Article routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes diversity mixing to ensure varied source distribution.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.core.logging import get_logger
from app.schemas.article import (
    Article as ArticleSchema, 
    ArticleWithConversation, 
    ArticleList, 
    TagCount
)
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentType
from app.services.diversity_mixer import mix_feed

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


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
    Results are diversity-mixed to ensure varied source distribution.
    """
    offset = (page - 1) * limit
    
    # Fetch more candidates for diversity mixing (2x target + buffer)
    fetch_limit = min(limit * 3, 100)
    
    items = content_repo.get_by_type(
        ContentType.ARTICLE,
        limit=fetch_limit,
        offset=offset,
        hours_back=720,  # 30 days - ensure enough content available
        ai_processed_only=True
    )
    
    if not items and page == 1:
        raise HTTPException(status_code=404, detail="No articles found")
    
    # Apply diversity mixing
    mixed_items = mix_feed(items, surface="articles", target_size=limit)
    
    articles = [_content_item_to_article_schema(item) for item in mixed_items]
    logger.info(f"Returning {len(articles)} diversity-mixed articles (page {page})")
    
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


@router.get("/{article_id}", response_model=ArticleWithConversation)
def get_article(
    article_id: int,
    content_repo: ContentItemRepository = Depends(get_content_repo)
):
    """Get a specific article by ID, including conversation history."""
    item = content_repo.get_by_id(article_id)
    if not item or item.type != ContentType.ARTICLE:
        raise not_found_exception("Article", article_id)
    
    article_data = _content_item_to_article_schema(item)
    article_data["conversations"] = []
    return article_data
