"""Helpers for recovering missing article hero images from source pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extraction.fetcher import fetch_url
from app.extraction.metadata import (
    PageMetadata,
    extract_metadata,
    is_probably_generic_image_url,
)
from app.models.content import ContentItem, ContentType

logger = get_logger(__name__)


def fetch_article_page_metadata(article_url: str) -> Optional[PageMetadata]:
    """Fetch and extract best-effort page metadata for an article URL."""
    if not article_url:
        return None

    try:
        fetch = fetch_url(article_url)
        if fetch.error or not fetch.html:
            return None
        return extract_metadata(fetch.html, fetch.url or article_url)
    except Exception as exc:
        logger.debug("Article metadata fetch failed for %s: %s", article_url, exc)
        return None


def repair_article_image_metadata(
    db: Session,
    *,
    lookback_days: int = 14,
    limit: int = 200,
    include_generic: bool = True,
) -> Dict[str, int]:
    """Backfill missing or suspicious article image/canonical metadata."""
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
        if _needs_article_metadata_repair(item, include_generic=include_generic):
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
        try:
            metadata = fetch_article_page_metadata(source_url)
        except Exception as exc:
            logger.warning("Article image repair failed for %s: %s", source_url, exc)
            failures += 1
            continue

        if metadata is None:
            continue

        changed = False
        if _should_replace_image(item.image_url, metadata.image_url):
            was_missing = not (item.image_url or "").strip()
            item.image_url = metadata.image_url
            changed = True
            if was_missing:
                filled_missing += 1
            else:
                replaced_generic += 1
        if not (item.canonical_url or "").strip() and metadata.canonical_url:
            item.canonical_url = metadata.canonical_url
            changed = True

        if changed:
            item.updated_at = datetime.utcnow()
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


def _needs_article_metadata_repair(item: ContentItem, *, include_generic: bool) -> bool:
    """Return True when a row should be revisited for image/canonical metadata."""
    has_missing_image = not (item.image_url or "").strip()
    has_missing_canonical = not (item.canonical_url or "").strip()
    has_generic_image = include_generic and is_probably_generic_image_url(item.image_url)
    return has_missing_image or has_missing_canonical or has_generic_image


def _should_replace_image(
    existing_image_url: Optional[str], candidate_image_url: Optional[str]
) -> bool:
    """Return True when the fetched image should replace the stored value."""
    if not candidate_image_url:
        return False

    existing = (existing_image_url or "").strip()
    if not existing:
        return True
    if existing == candidate_image_url:
        return False
    if is_probably_generic_image_url(existing):
        return True
    return False
