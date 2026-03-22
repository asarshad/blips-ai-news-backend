"""Helpers for recovering missing article hero images from source pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.article_hydration import ArticleHydrationService
from app.core.logging import get_logger
from app.extraction.metadata import PageMetadata, is_probably_generic_image_url
from app.models.content import ContentItem, ContentType

logger = get_logger(__name__)


def fetch_article_page_metadata(article_url: str) -> Optional[PageMetadata]:
    """Fetch and extract best-effort page metadata for an article URL."""
    return ArticleHydrationService.fetch_article_page_metadata(article_url)


def repair_article_image_metadata(
    db: Session,
    *,
    lookback_days: int = 14,
    limit: int = 200,
    include_generic: bool = True,
) -> Dict[str, int]:
    """Backfill missing or suspicious article image/canonical metadata."""
    hydrator = ArticleHydrationService()
    hydrator.fetch_article_page_metadata = fetch_article_page_metadata
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    recent_items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.published_at >= cutoff,
            ContentItem.source_url.isnot(None),
        )
        .order_by(ContentItem.published_at.desc())
        .all()
    )

    items = []
    for item in recent_items:
        if hydrator.needs_article_metadata_repair(item, include_generic=include_generic):
            items.append(item)
        if len(items) >= limit:
            break

    scanned = 0
    updated = 0
    failures = 0
    filled_missing = 0
    replaced_generic = 0
    pending_changes = 0

    for item in items:
        scanned += 1
        source_url = item.canonical_url or item.source_url or ""
        was_missing = not (item.image_url or "").strip()
        was_generic = bool(item.image_url) and is_probably_generic_image_url(item.image_url)
        try:
            changed = hydrator.refresh_existing_article_metadata(item, source_url=source_url)
        except Exception as exc:
            logger.warning("Article image repair failed for %s: %s", source_url, exc)
            failures += 1
            continue

        if not changed:
            continue

        if was_missing and (item.image_url or "").strip():
            filled_missing += 1
        elif was_generic and (item.image_url or "").strip():
            replaced_generic += 1

        updated += 1
        pending_changes += 1

        if pending_changes >= 25:
            db.commit()
            pending_changes = 0

    if pending_changes:
        db.commit()

    return {
        "scanned": scanned,
        "updated": updated,
        "failures": failures,
        "filled_missing": filled_missing,
        "replaced_generic": replaced_generic,
        "lookback_days": lookback_days,
    }
