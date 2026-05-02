"""Helpers for recovering missing article hero images from source pages."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.article_hydration import (
    ARTICLE_IMAGE_STATUS_PENDING,
    ArticleHydrationService,
    finalize_article_image_verification,
)
from app.core.logging import get_logger
from app.extraction.metadata import PageMetadata, is_probably_generic_image_url
from app.extraction.normalize import is_suspicious_image_url
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services.article_image_placeholder import (
    is_placeholder_image_url,
)
from app.services.content_readiness import sync_content_readiness
from app.services.playlist_service import refresh_cached_playlist_items

logger = get_logger(__name__)

ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE = "article.image_verification.requested"
_ARTICLE_IMAGE_PENDING_REASONS = {
    "awaiting_article_image_verification",
    "missing_article_image",
}


def fetch_article_page_metadata(article_url: str) -> Optional[PageMetadata]:
    """Fetch and extract best-effort page metadata for an article URL."""
    return ArticleHydrationService.fetch_article_page_metadata(article_url)


def should_queue_article_image_verification(item: Any) -> bool:
    """Return True when an article should be pushed onto the async image-verification lane."""
    if getattr(item, "id", None) is None:
        return False
    if getattr(item, "type", None) != ContentType.ARTICLE:
        return False
    if getattr(item, "curation_status", None) != ContentStatus.PROMOTED:
        return False
    readiness_reason = (getattr(item, "readiness_reason", None) or "").strip()
    return readiness_reason in _ARTICLE_IMAGE_PENDING_REASONS


def queue_article_image_verification_request(
    db: Session,
    item: Any,
    *,
    now: Optional[datetime] = None,
) -> ContentEventOutbox | None:
    """Enqueue a durable outbox event requesting article image verification."""
    if not should_queue_article_image_verification(item):
        return None

    existing = (
        db.query(ContentEventOutbox.id)
        .filter(
            ContentEventOutbox.content_item_id == int(item.id),
            ContentEventOutbox.event_type == ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
            ContentEventOutbox.status.in_(("pending", "processing")),
        )
        .first()
    )
    if existing is not None:
        return None

    event = ContentEventOutbox(
        content_item_id=int(item.id),
        event_type=ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
        payload={
            "content_id": int(item.id),
            "source_url": (
                getattr(item, "canonical_url", None) or getattr(item, "source_url", None) or ""
            ).strip()
            or None,
            "readiness_reason": (getattr(item, "readiness_reason", None) or "").strip() or None,
        },
        status="pending",
        available_at=now or datetime.utcnow(),
    )
    db.add(event)
    return event


def repair_article_image_metadata(
    db: Session,
    *,
    lookback_days: int = 14,
    limit: int = 200,
    max_seconds: int | None = None,
    include_generic: bool = True,
    promoted_only: bool = False,
    readiness_reasons: tuple[str, ...] | None = None,
) -> Dict[str, int]:
    """Backfill missing or suspicious article image/canonical metadata."""
    started_monotonic = time.monotonic()
    hydrator = ArticleHydrationService()
    hydrator.fetch_article_page_metadata = fetch_article_page_metadata
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    query = db.query(ContentItem).filter(
        ContentItem.type == ContentType.ARTICLE,
        ContentItem.published_at >= cutoff,
        ContentItem.source_url.isnot(None),
    )
    if promoted_only:
        query = query.filter(ContentItem.curation_status == ContentStatus.PROMOTED)
    if readiness_reasons:
        # Also pick up READY articles that have a relative placeholder URL (no scheme/host)
        # so they get upgraded to absolute URLs once API_PUBLIC_BASE_URL is configured.
        query = query.filter(
            or_(
                ContentItem.readiness_reason.in_(tuple(readiness_reasons)),
                ContentItem.image_url.like("/api/v1/placeholder/%"),
            )
        )

    recent_items = query.order_by(ContentItem.published_at.desc(), ContentItem.id.desc()).all()

    items = []
    for item in recent_items:
        needs_second_pass = (
            getattr(item, "article_image_status", "") or ""
        ).strip().upper() != "VERIFIED"
        if needs_second_pass or hydrator.needs_article_metadata_repair(
            item, include_generic=include_generic
        ):
            items.append(item)
        if len(items) >= limit:
            break

    scanned = 0
    updated = 0
    failures = 0
    filled_missing = 0
    replaced_generic = 0
    replaced_suspicious = 0
    verified_missing = 0
    placeholder_applied = 0
    pending_changes = 0
    stopped_due_time_limit = False

    for item in items:
        if max_seconds is not None and max_seconds > 0:
            if time.monotonic() - started_monotonic >= max_seconds:
                stopped_due_time_limit = True
                logger.info(
                    "Article image repair reached time budget max_seconds=%s scanned=%s limit=%s",
                    max_seconds,
                    scanned,
                    limit,
                )
                break
        scanned += 1
        source_url = item.canonical_url or item.source_url or ""
        was_missing = not (item.image_url or "").strip()
        was_generic = bool(item.image_url) and is_probably_generic_image_url(item.image_url)
        was_suspicious = bool(item.image_url) and is_suspicious_image_url(item.image_url)
        previous_verification_status = (getattr(item, "article_image_status", None) or "").strip()
        previous_readiness_status = (getattr(item, "readiness_status", None) or "").strip()
        try:
            changed = hydrator.refresh_existing_article_metadata(
                item,
                source_url=source_url,
                force_reconcile_image=(
                    (getattr(item, "article_image_status", "") or "").strip().upper()
                    == ARTICLE_IMAGE_STATUS_PENDING
                ),
            )
        except Exception as exc:
            logger.warning("Article image repair failed for %s: %s", source_url, exc)
            failures += 1
            continue

        previous_image_url = (item.image_url or "").strip()
        finalize_article_image_verification(item, allow_placeholder_fallback=True)
        if is_placeholder_image_url(item.image_url) and item.image_url != previous_image_url:
            placeholder_applied += 1
            changed = True
        sync_content_readiness(db, item)
        verification_changed = (
            getattr(item, "article_image_status", None) or ""
        ).strip() != previous_verification_status
        readiness_changed = (
            getattr(item, "readiness_status", None) or ""
        ).strip() != previous_readiness_status
        changed = bool(changed or verification_changed or readiness_changed)

        if not changed:
            continue

        if was_missing and (item.image_url or "").strip():
            filled_missing += 1
        elif was_generic and (item.image_url or "").strip():
            replaced_generic += 1
        elif was_suspicious and (item.image_url or "").strip():
            replaced_suspicious += 1
        elif not (item.image_url or "").strip():
            verified_missing += 1

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
        "replaced_suspicious": replaced_suspicious,
        "verified_missing": verified_missing,
        "placeholder_applied": placeholder_applied,
        "stopped_due_time_limit": int(stopped_due_time_limit),
        "lookback_days": lookback_days,
    }


def repair_single_article_image(
    db: Session,
    *,
    content_id: int,
) -> Dict[str, Any]:
    """Force-refresh image metadata for a specific article row."""
    hydrator = ArticleHydrationService()
    hydrator.fetch_article_page_metadata = fetch_article_page_metadata

    item = db.get(ContentItem, int(content_id))
    if item is None or item.type != ContentType.ARTICLE:
        raise ValueError(f"Article {content_id} not found")

    previous_image_url = (item.image_url or "").strip() or None
    previous_verification_status = (
        getattr(item, "article_image_status", None) or ""
    ).strip() or None
    previous_readiness_status = (getattr(item, "readiness_status", None) or "").strip() or None
    source_url = (item.canonical_url or item.source_url or "").strip()

    changed = hydrator.refresh_existing_article_metadata(
        item,
        source_url=source_url,
        force_reconcile_image=True,
    )
    finalize_article_image_verification(item, allow_placeholder_fallback=True)
    sync_content_readiness(db, item)
    db.commit()
    db.refresh(item)
    cache_refresh = refresh_cached_playlist_items(db, content_ids=[item.id])

    return {
        "content_id": item.id,
        "source_url": source_url or None,
        "changed": bool(changed or (item.image_url or "").strip() != (previous_image_url or "")),
        "previous_image_url": previous_image_url,
        "image_url": (item.image_url or "").strip() or None,
        "previous_article_image_status": previous_verification_status,
        "article_image_status": (getattr(item, "article_image_status", None) or "").strip() or None,
        "previous_readiness_status": previous_readiness_status,
        "readiness_status": (getattr(item, "readiness_status", None) or "").strip() or None,
        "cache_refresh": cache_refresh,
    }


def process_article_image_verification_request(
    db: Session,
    *,
    content_id: int,
) -> Dict[str, Any]:
    """Handle one durable article image-verification request inside the worker transaction."""
    hydrator = ArticleHydrationService()
    hydrator.fetch_article_page_metadata = fetch_article_page_metadata

    item = db.get(ContentItem, int(content_id))
    if item is None or item.type != ContentType.ARTICLE:
        raise ValueError(f"Article {content_id} not found")

    previous_image_url = (item.image_url or "").strip() or None
    previous_verification_status = (
        getattr(item, "article_image_status", None) or ""
    ).strip() or None
    previous_readiness_status = (getattr(item, "readiness_status", None) or "").strip() or None
    previous_readiness_reason = (getattr(item, "readiness_reason", None) or "").strip() or None
    source_url = (item.canonical_url or item.source_url or "").strip()

    changed = hydrator.refresh_existing_article_metadata(
        item,
        source_url=source_url,
        force_reconcile_image=True,
    )
    finalize_article_image_verification(item, allow_placeholder_fallback=True)
    sync_content_readiness(db, item)

    return {
        "content_id": item.id,
        "source_url": source_url or None,
        "changed": bool(changed or (item.image_url or "").strip() != (previous_image_url or "")),
        "previous_image_url": previous_image_url,
        "image_url": (item.image_url or "").strip() or None,
        "placeholder_applied": is_placeholder_image_url(item.image_url),
        "previous_article_image_status": previous_verification_status,
        "article_image_status": (getattr(item, "article_image_status", None) or "").strip() or None,
        "previous_readiness_status": previous_readiness_status,
        "readiness_status": (getattr(item, "readiness_status", None) or "").strip() or None,
        "previous_readiness_reason": previous_readiness_reason,
        "readiness_reason": (getattr(item, "readiness_reason", None) or "").strip() or None,
    }


def evaluate_llm_article_image_recovery(
    db: Session,
    *,
    lookback_days: Optional[int] = None,
    limit: int = 200,
    sample_size: int = 20,
    apply: bool = False,
) -> Dict[str, Any]:
    """Measure the LLM fallback hit rate on promoted article rows with blank images."""
    hydrator = ArticleHydrationService()
    blank_image = func.length(func.trim(func.coalesce(ContentItem.image_url, ""))) == 0
    query = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.source_url.isnot(None),
            blank_image,
        )
        .order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
    )

    if lookback_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=max(1, int(lookback_days)))
        query = query.filter(ContentItem.published_at >= cutoff)

    if int(limit) > 0:
        query = query.limit(max(1, int(limit)))

    items = query.all()
    scanned = 0
    recovered = 0
    applied_count = 0
    failures = 0
    pending_changes = 0
    reason_counts: dict[str, int] = defaultdict(int)
    domain_totals: dict[str, int] = defaultdict(int)
    domain_recovered: dict[str, int] = defaultdict(int)
    success_examples: list[dict[str, Any]] = []
    failure_examples: list[dict[str, Any]] = []
    max_examples = max(0, int(sample_size))

    for item in items:
        scanned += 1
        host = _host_for_item(item)
        domain_totals[host] += 1
        try:
            extraction = hydrator.extract_article_image_with_llm_diagnostics(
                article_url=(item.canonical_url or item.source_url or "").strip(),
                title=item.title,
            )
        except Exception as exc:
            failures += 1
            reason_counts["unexpected_exception"] += 1
            if len(failure_examples) < max_examples:
                row = _serialize_item(item)
                row["reason"] = "unexpected_exception"
                row["error"] = str(exc)
                failure_examples.append(row)
            continue

        reason_counts[extraction.reason] += 1
        image_url = extraction.image_url
        if image_url:
            recovered += 1
            domain_recovered[host] += 1
            if len(success_examples) < max_examples:
                success_row = _serialize_item(item, recovered_image_url=image_url)
                success_row["reason"] = extraction.reason
                if extraction.raw_candidate_url:
                    success_row["raw_candidate_url"] = extraction.raw_candidate_url
                success_examples.append(success_row)

            if apply:
                item.image_url = image_url
                finalize_article_image_verification(item)
                sync_content_readiness(db, item)
                applied_count += 1
                pending_changes += 1
                if pending_changes >= 25:
                    db.commit()
                    pending_changes = 0
        else:
            if len(failure_examples) < max_examples:
                row = _serialize_item(item)
                row["reason"] = extraction.reason
                if extraction.raw_candidate_url:
                    row["raw_candidate_url"] = extraction.raw_candidate_url
                if extraction.error:
                    row["error"] = extraction.error
                failure_examples.append(row)

    if apply and pending_changes:
        db.commit()

    domain_breakdown = []
    for host, total in sorted(domain_totals.items(), key=lambda row: (-row[1], row[0])):
        host_recovered = domain_recovered.get(host, 0)
        domain_breakdown.append(
            {
                "host": host,
                "scanned": total,
                "recovered": host_recovered,
                "success_rate_percent": round((host_recovered / total) * 100.0, 1)
                if total
                else 0.0,
            }
        )

    return {
        "scanned": scanned,
        "recovered": recovered,
        "not_recovered": max(0, scanned - recovered - failures),
        "failures": failures,
        "success_rate_percent": round((recovered / scanned) * 100.0, 1) if scanned else 0.0,
        "applied": applied_count,
        "lookback_days": lookback_days,
        "limit": limit,
        "sample_size": sample_size,
        "reason_counts": dict(sorted(reason_counts.items())),
        "domain_breakdown": domain_breakdown,
        "success_examples": success_examples,
        "failure_examples": failure_examples,
    }


def _host_for_item(item: ContentItem) -> str:
    url = (item.canonical_url or item.source_url or "").strip()
    return (urlparse(url).hostname or "").lower() or "unknown"


def _serialize_item(
    item: ContentItem,
    *,
    recovered_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": item.id,
        "title": (item.title or "").strip(),
        "source": (item.source or "").strip() or None,
        "host": _host_for_item(item),
        "source_url": (item.source_url or "").strip() or None,
        "canonical_url": (item.canonical_url or "").strip() or None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "article_image_status": (getattr(item, "article_image_status", None) or "").strip() or None,
        "recovered_image_url": recovered_image_url,
    }
