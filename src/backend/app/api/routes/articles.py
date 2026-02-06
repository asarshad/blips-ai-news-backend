"""Article routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes tiered freshness strategy (A/B/C) and diversity mixing.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.schemas.article import (
    Article as ArticleSchema, 
    ArticleWithConversation, 
    ArticleList, 
    TagCount
)
from app.repositories.content_repo import ContentItemRepository
from app.models.content import ContentType
from app.services.diversity_mixer import mix_feed
from app.services.inventory_service import Surface
from app.services.tiered_feed_service import (
    get_tiered_feed,
    get_cached_tiered_feed,
    tiered_item_to_dict,
)
from app.services.topup_service import check_and_trigger_topup

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


@router.get("/recent", response_model=Dict[str, Any])
def get_recent_articles(
    limit: int = Query(5, ge=1, le=50, description="Number of articles to return"),
    page: int = Query(1, ge=1, description="Page number"),
    db: Session = Depends(get_db)
):
    """
    Get the most recent articles using tiered freshness strategy.
    
    Returns a blend of:
    - Tier A (Fresh): articles published within rolling window
    - Tier B (Backfill): articles added recently but published earlier
    - Tier C (Evergreen): older high-quality articles
    
    Each article includes freshness_tier, published_age_seconds, and added_age_seconds.
    Results are diversity-mixed and cached (45s TTL) for performance.
    """
    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)
    
    offset = (page - 1) * limit
    
    # Use cached tiered feed for better performance
    articles, has_more = get_cached_tiered_feed(
        db,
        Surface.ARTICLES,
        limit=limit,
        offset=offset,
        require_ai_processed=True,
    )
    
    if not articles and page == 1:
        raise HTTPException(status_code=404, detail="No articles found")
    
    # Log tier distribution (from cached results)
    tier_counts = {}
    for a in articles:
        tier = a.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Articles page {page}: {tier_counts} (limit={limit})")
    
    return {
        "articles": articles,
        "has_more": has_more,
        "page": page,
    }


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
