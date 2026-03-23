"""Article routes for the REST API.

Updated to serve content from the unified content_items table with AI filtering.
Includes tiered freshness strategy (A/B/C) and diversity mixing.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.feed_headers import FeedMetadata, compute_feed_version
from app.core.config import settings
from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content import ContentType
from app.ranking.feed_score import rerank_feed
from app.repositories.content_repo import ContentItemRepository
from app.repositories.user_repo import UserCategorySelectionRepository
from app.schemas.article import ArticleWithConversation
from app.services.ad_mixer import inject_ads
from app.services.content_payloads import content_item_to_article_payload
from app.services.freshness_metrics_service import record_feed_served
from app.services.inventory_service import Surface
from app.services.tiered_feed_service import (
    get_cached_tiered_feed,
)
from app.services.topup_service import check_and_trigger_topup

logger = get_logger(__name__)
router = APIRouter()


def get_content_repo(db: Session = Depends(get_db)) -> ContentItemRepository:
    """Factory for ContentItemRepository."""
    return ContentItemRepository(db)


def _content_item_to_article_schema(item) -> dict:
    """Convert ContentItem to Article schema format."""
    return content_item_to_article_payload(item)


@router.get("/recent", response_model=Dict[str, Any])
def get_recent_articles(
    response: Response,
    limit: int = Query(5, ge=1, le=50, description="Number of articles to return"),
    page: int = Query(1, ge=1, description="Page number"),
    db: Session = Depends(get_db),
    x_device_id: Optional[str] = Header(None, alias="X-Device-ID"),
):
    """
    Get the most recent articles using tiered freshness strategy.

    Returns a blend of:
    - Tier A (Fresh): articles published within rolling window
    - Tier B (Backfill): articles added recently but published earlier
    - Tier C (Evergreen): older high-quality articles

    Each article includes freshness_tier, published_age_seconds, and added_age_seconds.
    Results are diversity-mixed and cached (45s TTL) for performance.

    If X-Device-ID header is present, results are personalised:
    - Items matching the user's declared category interests receive an interest_boost.
    - Stale items receive a staleness_decay penalty.
    - Over-represented sources receive a source_dominance_penalty.

    Response headers include diagnostic info:
    - X-Feed-Generated-At, X-Feed-Source, X-Cache, X-Newest-Published-At, etc.
    """
    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)

    offset = (page - 1) * limit

    # Use cached tiered feed for better performance
    # Only show articles that have been AI-processed (have summaries)
    articles, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.ARTICLES,
        limit=limit,
        offset=offset,
        require_ai_processed=True,
    )

    # ── Personalised re-ranking ───────────────────────────────────────────
    # Apply per-user interest_boost / staleness / dominance adjustments.
    # This happens AFTER the cached retrieval, so the DB-level cache is
    # shared across all users and only the lightweight in-memory sort is
    # per-user.
    if x_device_id and articles:
        cat_repo = UserCategorySelectionRepository(db)
        selected = cat_repo.get_selected_categories(x_device_id)
        total_weight = cat_repo.get_total_learned_weight(x_device_id)
        if selected:
            articles = rerank_feed(
                items=articles,
                selected_categories=selected,
                total_learned_weight=total_weight,
                window_size=max(len(articles), 1),
            )
            response.headers["X-Personalized"] = "true"
            response.headers["X-Selected-Categories"] = ",".join(selected[:5])
        else:
            response.headers["X-Personalized"] = "false"
    # ─────────────────────────────────────────────────────────────────────

    # Log tier distribution (from cached results)
    tier_counts = {}
    for a in articles:
        tier = a.get("freshness_tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(f"Articles page {page}: {tier_counts} (limit={limit})")

    # ALWAYS add diagnostic headers (even on empty results) so debugging is
    # possible.  Previously a 404 was raised before headers were set.
    feed_meta = FeedMetadata(
        generated_at=meta.generated_at,
        source=meta.source,
        cache_key=meta.cache_key,
        cache_hit=meta.cache_hit,
        items=articles,
        surface="articles",
        tier_config=meta.tier_config,
        feed_version=compute_feed_version(articles, meta.generated_at),
    )
    feed_meta.add_headers(response)

    if not articles and page == 1:
        raise HTTPException(status_code=404, detail="No articles found")

    # Ad injection (noop when ADS_ENABLED is false)
    mixed, ads_injected = inject_ads(articles, placement_id="feed_fullpage")
    response.headers["X-Ads-Injected"] = str(ads_injected)
    response.headers["X-Ads-Frequency"] = str(settings.ADS_FEED_FREQUENCY)
    newest_published_at, newest_created_at = feed_meta.get_newest_dates()
    inventory_state = "healthy" if articles else "warming_up"
    record_feed_served(
        surface="articles",
        feed_version=feed_meta.feed_version,
        inventory_state=inventory_state,
        items=articles,
    )

    return {
        "articles": mixed,
        "has_more": has_more,
        "page": page,
        "served_at": meta.generated_at.isoformat(),
        "inventory_state": inventory_state,
        "feed_version": feed_meta.feed_version,
        "newest_published_at": newest_published_at,
        "newest_created_at": newest_created_at,
        "remaining_count": getattr(meta, "remaining_window_count", 0),
    }


@router.get("/{article_id}", response_model=ArticleWithConversation)
def get_article(article_id: int, content_repo: ContentItemRepository = Depends(get_content_repo)):
    """Get a specific article by ID, including conversation history."""
    item = content_repo.get_by_id(article_id)
    if not item or item.type != ContentType.ARTICLE:
        raise not_found_exception("Article", article_id)

    article_data = _content_item_to_article_schema(item)
    article_data["conversations"] = []
    return article_data
