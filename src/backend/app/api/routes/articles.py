"""Article routes for the REST API.

Serves article payloads from the unified content_items table using the shared
readiness contract.
Includes tiered freshness strategy (A/B/C) and diversity mixing.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.feed_headers import FeedMetadata, compute_feed_version
from app.core.config import settings
from app.core.dependencies import get_db
from app.core.exceptions import not_found_exception
from app.core.logging import get_logger
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.db.base import SessionLocal
from app.models.content import ContentType
from app.repositories.content_repo import ContentItemRepository
from app.schemas.article import ArticleWithConversation
from app.services.ad_mixer import inject_ads
from app.services.content_payloads import content_item_to_article_payload
from app.services.content_readiness import is_ready_for_surface
from app.services.freshness_metrics_service import record_feed_served
from app.services.inventory_service import Surface
from app.services.tiered_feed_service import (
    get_cached_tiered_feed,
)
from app.services.topup_service import check_and_trigger_topup

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_session_token)])


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
    session: AuthenticatedSession = Depends(require_session_token),
):
    """
    Get the most recent articles using tiered freshness strategy.

    Returns a blend of:
    - Tier A (Fresh): articles published within rolling window
    - Tier B (Backfill): articles added recently but published earlier
    - Tier C (Evergreen): older high-quality articles

    Each article includes freshness_tier, published_age_seconds, and added_age_seconds.
    Results are diversity-mixed and cached (45s TTL) for performance.

    Results are personalised using the caller's anonymous bearer session:
    - Items matching the user's declared category interests receive an interest_boost.
    - Stale items receive a staleness_decay penalty.
    - Over-represented sources receive a source_dominance_penalty.

    Response headers include diagnostic info:
    - X-Feed-Generated-At, X-Feed-Source, X-Cache, X-Newest-Published-At, etc.
    """
    # Check inventory and trigger background top-up if needed (non-blocking)
    check_and_trigger_topup(db, SessionLocal)

    offset = (page - 1) * limit

    # Use cached tiered feed for better performance.
    # The readiness contract guarantees article quality here and for push.
    articles, has_more, meta = get_cached_tiered_feed(
        db,
        Surface.ARTICLES,
        limit=limit,
        offset=offset,
        device_id=session.device_id,
    )

    response.headers["X-Personalized"] = "false"

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
        strategy_name=meta.strategy_name,
        strategy_source=meta.strategy_source,
        resume_continuity_window_minutes=meta.resume_continuity_window_minutes,
        resume_snapshot_after_remote_window=meta.resume_snapshot_after_remote_window,
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
    if not is_ready_for_surface(item, Surface.ARTICLES.value):
        raise not_found_exception("Article", article_id)

    article_data = _content_item_to_article_schema(item)
    article_data["conversations"] = []
    return article_data
