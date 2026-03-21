"""Helpers for recovering missing article hero images from source pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extraction.fetcher import fetch_url
from app.extraction.metadata import PageMetadata, extract_metadata
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
        return extract_metadata(fetch.html, article_url)
    except Exception as exc:
        logger.debug("Article metadata fetch failed for %s: %s", article_url, exc)
        return None


def repair_article_image_metadata(
    db: Session,
    *,
    lookback_days: int = 14,
    limit: int = 200,
) -> Dict[str, int]:
    """Backfill missing article image/canonical metadata for recent rows."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.published_at >= cutoff,
            ContentItem.source_url.isnot(None),
            or_(ContentItem.image_url.is_(None), ContentItem.image_url == ""),
        )
        .order_by(ContentItem.published_at.desc())
        .limit(limit)
        .all()
    )

    scanned = 0
    updated = 0
    failures = 0

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
        if not (item.image_url or "").strip() and metadata.image_url:
            item.image_url = metadata.image_url
            changed = True
        if not (item.canonical_url or "").strip() and metadata.canonical_url:
            item.canonical_url = metadata.canonical_url
            changed = True

        if changed:
            item.updated_at = datetime.utcnow()
            updated += 1

    if updated:
        db.commit()

    return {
        "scanned": scanned,
        "updated": updated,
        "failures": failures,
        "lookback_days": lookback_days,
    }
