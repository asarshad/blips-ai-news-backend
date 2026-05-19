#!/usr/bin/env python3
"""Stable operator-run repair/backfill hook.

Replace this module's `run()` implementation whenever we need a new
one-off repair or backfill, while keeping the same admin API endpoint.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.integrations.llm_client import LLMClient
from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.services.article_image_service import repair_article_image_metadata
from app.services.article_quality_policy import classify_article_quality_block
from app.services.article_unskimmable_service import ARTICLE_UNSKIMMABLE_TERMINAL_REASON
from app.services.content_event_backfill_service import enqueue_pending_content_events
from app.services.content_readiness import seed_content_readiness, sync_content_readiness
from app.services.major_news_constants import MAJOR_NEWS_DISCOVERED_VIA
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.tiered_feed_service import invalidate_tiered_feed_cache


def _append_block_reason(existing: str | None, reason: str) -> str:
    base = (existing or "operator_quality_gate").strip() or "operator_quality_gate"
    marker = f"|blocked={reason}"
    if marker in base:
        return base[:255]
    return f"{base}{marker}"[:255]


def _run_article_quality_gate_backfill(db, options: dict[str, Any]) -> dict[str, Any]:
    lookback_days = max(1, int(options.get("lookback_days", 14)))
    limit = max(1, int(options.get("limit", 300)))
    reclassify_missing = bool(options.get("reclassify_missing", True))
    dry_run = bool(options.get("dry_run", False))
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.created_at >= cutoff,
            ContentItem.is_suppressed.is_(False),
        )
        .order_by(ContentItem.created_at.desc())
        .limit(limit)
        .all()
    )

    llm_client = LLMClient()
    can_classify = reclassify_missing and llm_client.is_configured()
    touched_ids: list[int] = []
    deterministic_blocked = 0
    reclassified = 0
    reclassified_non_tech = 0
    classification_errors = 0
    resynced = 0

    for item in items:
        changed = False
        block = classify_article_quality_block(item)
        if block is not None:
            deterministic_blocked += 1
            if not dry_run:
                item.curation_status = ContentStatus.CANDIDATE
                item.tech_relevance = "no"
                item.tech_relevance_confidence = 1.0
                item.tech_relevance_reason = block.detail[:255]
                item.promotion_reason = _append_block_reason(item.promotion_reason, block.reason)
                changed = True
        elif can_classify and not (item.tech_relevance or "").strip():
            summary = (item.summary or item.description or item.title or "").strip()
            if summary:
                try:
                    tech = llm_client.classify_blips_tech_relevance(
                        title=item.title or "",
                        summary=summary,
                        source=item.source or "",
                        url=item.source_url or None,
                    )
                except Exception:
                    classification_errors += 1
                    continue
                reclassified += 1
                if tech.is_blips_tech_relevant == "no":
                    reclassified_non_tech += 1
                if not dry_run:
                    item.tech_relevance = tech.is_blips_tech_relevant
                    item.tech_relevance_confidence = tech.confidence
                    item.tech_relevance_reason = tech.reason
                    if tech.is_blips_tech_relevant == "no":
                        item.curation_status = ContentStatus.CANDIDATE
                        item.promotion_reason = _append_block_reason(
                            item.promotion_reason,
                            "llm_non_tech_article",
                        )
                    changed = True

        if not dry_run:
            before = getattr(item, "readiness_reason", None)
            sync_content_readiness(db, item)
            if before != getattr(item, "readiness_reason", None):
                changed = True
                resynced += 1
            if changed and item.id is not None:
                touched_ids.append(int(item.id))

    if not dry_run:
        db.commit()
        invalidate_tiered_feed_cache()
        cache_refresh = (
            refresh_cached_playlist_items(db, content_ids=touched_ids) if touched_ids else {}
        )
    else:
        cache_refresh = {}

    return {
        "job": "article_quality_gate_backfill",
        "lookback_days": lookback_days,
        "limit": limit,
        "dry_run": dry_run,
        "scanned": len(items),
        "deterministic_blocked": deterministic_blocked,
        "reclassified": reclassified,
        "reclassified_non_tech": reclassified_non_tech,
        "classification_errors": classification_errors,
        "resynced": resynced,
        "touched_ids": touched_ids[:200],
        "cache_busted": not dry_run,
        "playlist_cache_refresh": cache_refresh,
    }


def _run_major_news_stale_probe_cleanup(db, options: dict[str, Any]) -> dict[str, Any]:
    max_age_hours = max(1, int(options.get("max_age_hours", 48)))
    limit = max(1, int(options.get("limit", 200)))
    dry_run = bool(options.get("dry_run", False))
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.curation_status == ContentStatus.CANDIDATE,
            ContentItem.readiness_status == ContentReadinessStatus.PENDING.value,
            ContentItem.readiness_reason == "awaiting_promotion",
            ContentItem.published_at < cutoff,
            ContentItem.is_suppressed.is_(False),
            (
                (ContentItem.discovered_via == MAJOR_NEWS_DISCOVERED_VIA)
                | (ContentItem.is_major_tech_news.is_(True))
            ),
        )
        .order_by(ContentItem.published_at.asc(), ContentItem.id.asc())
        .limit(limit)
        .all()
    )

    touched_ids: list[int] = []
    resynced = 0
    for item in items:
        if item.id is not None:
            touched_ids.append(int(item.id))
        if dry_run:
            continue

        item.is_major_tech_news = False
        item.major_tech_news_reason = "stale_major_news_probe_item: outside promotion window"
        item.promotion_reason = _append_block_reason(
            item.promotion_reason,
            "stale_major_news_probe_item",
        )
        before = getattr(item, "readiness_reason", None)
        sync_content_readiness(db, item)
        if before != getattr(item, "readiness_reason", None):
            resynced += 1

    if not dry_run:
        db.commit()
        invalidate_tiered_feed_cache()
        cache_refresh = (
            refresh_cached_playlist_items(db, content_ids=touched_ids) if touched_ids else {}
        )
    else:
        cache_refresh = {}

    return {
        "job": "major_news_stale_probe_cleanup",
        "max_age_hours": max_age_hours,
        "limit": limit,
        "dry_run": dry_run,
        "scanned": len(items),
        "cleared_major_news_count": 0 if dry_run else len(items),
        "resynced": resynced,
        "touched_ids": touched_ids[:200],
        "cache_busted": not dry_run,
        "playlist_cache_refresh": cache_refresh,
    }


def _run_repair_unskimmable_with_description(db, options: dict[str, Any]) -> dict[str, Any]:
    """Re-queue articles that were terminally rejected as unskimmable but have a usable RSS description."""
    min_desc_words = max(1, int(options.get("min_desc_words", 20)))
    lookback_days = max(1, int(options.get("lookback_days", 90)))
    limit = max(1, int(options.get("limit", 500)))
    source = options.get("source") or None
    dry_run = bool(options.get("dry_run", False))

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    terminal_prefix = f"{ARTICLE_UNSKIMMABLE_TERMINAL_REASON}:max_unskimmable_attempts"

    query = db.query(ContentItem).filter(
        ContentItem.type == ContentType.ARTICLE,
        ContentItem.is_suppressed.is_(True),
        ContentItem.curation_status == ContentStatus.CANDIDATE,
        ContentItem.tech_relevance_reason.like(f"{terminal_prefix}%"),
        ContentItem.published_at >= cutoff,
    )
    if source:
        query = query.filter(ContentItem.source == source)

    candidates = query.order_by(ContentItem.published_at.desc()).limit(limit * 5).all()

    qualified = [
        item
        for item in candidates
        if len((item.description or "").strip().split()) >= min_desc_words
    ]

    repaired_ids: list[int] = []
    if not dry_run:
        for item in qualified[:limit]:
            item.is_suppressed = False
            item.ai_processed = False
            item.summary = None
            item.promotion_score = None
            item.tech_relevance_reason = None
            item.promotion_reason = None
            item.updated_at = datetime.utcnow()
            seed_content_readiness(item)
            if item.id is not None:
                repaired_ids.append(int(item.id))
        db.commit()

    return {
        "job": "repair_unskimmable_with_description",
        "dry_run": dry_run,
        "min_desc_words": min_desc_words,
        "lookback_days": lookback_days,
        "limit": limit,
        "source": source,
        "scanned": len(candidates),
        "qualified": len(qualified),
        "repaired": 0 if dry_run else len(repaired_ids),
        "repaired_ids": repaired_ids[:200],
    }


def _run_terminal_reject_stale_unskimmable_retry(db, options: dict[str, Any]) -> dict[str, Any]:
    """Terminal-reject articles stuck in article_unskimmable_retry that are too old to recover.

    These accumulated under the old retry-deferral path (ai_processed=False, not suppressed).
    Any article older than max_age_days with readiness_reason='article_unskimmable_retry'
    will never become extractable — reject them directly rather than letting the AI pipeline
    retry them indefinitely and spike memory.
    """
    from app.services.article_unskimmable_service import reject_terminal_unskimmable_article

    max_age_days = max(1, int(options.get("max_age_days", 7)))
    limit = max(1, int(options.get("limit", 2000)))
    dry_run = bool(options.get("dry_run", False))
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.is_suppressed.is_(False),
            ContentItem.ai_processed.is_(False),
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.readiness_reason == "article_unskimmable_retry",
            ContentItem.published_at < cutoff,
        )
        .order_by(ContentItem.published_at.asc())
        .limit(limit)
        .all()
    )

    rejected_ids: list[int] = []
    if not dry_run:
        for item in items:
            reject_terminal_unskimmable_article(db, item, reason="stale_unskimmable_retry")
            if item.id is not None:
                rejected_ids.append(int(item.id))
        db.commit()

    return {
        "job": "terminal_reject_stale_unskimmable_retry",
        "max_age_days": max_age_days,
        "limit": limit,
        "dry_run": dry_run,
        "scanned": len(items),
        "rejected": 0 if dry_run else len(rejected_ids),
        "rejected_ids": rejected_ids[:200],
    }


def run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute the current operator maintenance job and return its result."""
    options = dict(payload or {})
    job = str(options.get("job", "article_image_backfill")).strip() or "article_image_backfill"
    lookback_days = max(1, int(options.get("lookback_days", 7)))
    limit = max(1, int(options.get("limit", 300)))

    db = SessionLocal()
    try:
        if job == "content_event_backfill":
            pending_only = bool(options.get("pending_only", True))
            return enqueue_pending_content_events(
                db,
                lookback_days=lookback_days,
                limit=limit,
                pending_only=pending_only,
            )

        if job == "article_quality_gate_backfill":
            return _run_article_quality_gate_backfill(db, options)

        if job == "major_news_stale_probe_cleanup":
            return _run_major_news_stale_probe_cleanup(db, options)

        if job == "repair_unskimmable_with_description":
            return _run_repair_unskimmable_with_description(db, options)

        if job == "terminal_reject_stale_unskimmable_retry":
            return _run_terminal_reject_stale_unskimmable_retry(db, options)

        all_statuses = bool(options.get("all_statuses", False))
        all_reasons = bool(options.get("all_reasons", False))

        readiness_reasons = None
        if not all_reasons:
            readiness_reasons = (
                "missing_article_image",
                "awaiting_article_image_verification",
            )

        result = repair_article_image_metadata(
            db,
            lookback_days=lookback_days,
            limit=limit,
            include_generic=True,
            promoted_only=not all_statuses,
            readiness_reasons=readiness_reasons,
        )
        return {
            "job": "article_image_backfill",
            "lookback_days": lookback_days,
            "limit": limit,
            "promoted_only": not all_statuses,
            "readiness_reasons": list(readiness_reasons) if readiness_reasons else None,
            "repair_result": result,
        }
    finally:
        db.close()


def main() -> int:
    """CLI entry point for local operator use."""
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
