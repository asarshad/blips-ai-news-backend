#!/usr/bin/env python3
"""
Repair persisted video summaries that accidentally stored classifier JSON/text blobs.

Usage:
    python scripts/repair_broken_video_summaries.py --dry-run
    python scripts/repair_broken_video_summaries.py --hours-back 720 --limit 250
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.integrations.llm_client import (
    LLMClient,
    is_video_summary_acceptable,
    looks_like_video_classifier_payload,
    normalize_video_summary_output,
)
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.content_readiness import sync_content_readiness
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

logger = get_logger(__name__)


@dataclass
class RepairStats:
    matched: int = 0
    scanned: int = 0
    regenerated: int = 0
    excluded: int = 0
    skipped: int = 0
    failed: int = 0


def summary_needs_repair(summary_text: Optional[str]) -> bool:
    """Return True when a persisted video summary looks like leaked classifier output."""
    return looks_like_video_classifier_payload(summary_text)


def find_matching_broken_video_summary_ids(
    *,
    hours_back: int | None = None,
    limit: int = 250,
) -> list[int]:
    """Return IDs of promoted video rows whose stored summaries need repair."""
    db = SessionLocal()
    try:
        query = (
            db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.summary.isnot(None),
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(ContentItem.published_at.desc())
        )
        if hours_back is not None:
            cutoff = datetime.utcnow() - timedelta(hours=max(1, int(hours_back)))
            query = query.filter(ContentItem.published_at >= cutoff)
        if int(limit) > 0:
            query = query.limit(max(1, int(limit)))

        return [item.id for item in query.all() if summary_needs_repair(item.summary)]
    finally:
        db.close()


def _exclude_item(db, item: ContentItem) -> None:
    """Clear a broken summary so the item stays pending instead of shipping junk."""
    item.ai_processed = False
    item.summary = None
    item.conversation_starters = None
    item.tech_relevance = None
    item.tech_relevance_confidence = None
    item.tech_relevance_reason = None
    item.is_mixed_roundup = None
    item.updated_at = datetime.utcnow()
    sync_content_readiness(db, item)
    db.commit()


def repair_broken_video_summaries(
    *,
    hours_back: int | None = None,
    limit: int = 250,
) -> RepairStats:
    """Re-run summarization for recent promoted videos with leaked classifier payloads."""
    db = SessionLocal()
    stats = RepairStats()
    touched_content_ids: set[int] = set()

    try:
        llm_client = LLMClient()
        if not llm_client.is_configured():
            raise RuntimeError(f"{llm_client.get_provider()} API key is not configured")

        query = (
            db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.summary.isnot(None),
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(ContentItem.published_at.desc())
        )
        if hours_back is not None:
            cutoff = datetime.utcnow() - timedelta(hours=max(1, int(hours_back)))
            query = query.filter(ContentItem.published_at >= cutoff)
        if int(limit) > 0:
            query = query.limit(max(1, int(limit)))

        items = [item for item in query.all() if summary_needs_repair(item.summary)]
        stats.matched = len(items)

        for item in items:
            stats.scanned += 1
            text = item.content_text or item.description or item.title
            if not (text or "").strip():
                logger.info("Excluding video %s: no usable text for repair", item.id)
                _exclude_item(db, item)
                touched_content_ids.add(int(item.id))
                stats.excluded += 1
                continue

            try:
                result = llm_client.summarize_video(item.title, text)
                summary = normalize_video_summary_output(result.summary)
                item.tech_relevance = result.tech_relevance
                item.tech_relevance_confidence = result.tech_relevance_confidence
                item.tech_relevance_reason = result.tech_relevance_reason
                item.is_mixed_roundup = result.is_mixed_roundup
                item.updated_at = datetime.utcnow()

                classification_only = (
                    getattr(item, "tech_relevance", None) == "none"
                    or bool(getattr(item, "is_mixed_roundup", False))
                )
                if classification_only:
                    item.ai_processed = True
                    item.summary = None
                    item.conversation_starters = None
                    sync_content_readiness(db, item)
                    db.commit()
                    touched_content_ids.add(int(item.id))
                    stats.excluded += 1
                    continue

                if not is_video_summary_acceptable(summary):
                    logger.warning(
                        "Excluding video %s: regenerated summary still failed validation",
                        item.id,
                    )
                    _exclude_item(db, item)
                    touched_content_ids.add(int(item.id))
                    stats.excluded += 1
                    continue

                item.ai_processed = True
                item.summary = summary
                item.conversation_starters = result.conversation_starters
                sync_content_readiness(db, item)
                db.commit()
                touched_content_ids.add(int(item.id))
                stats.regenerated += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("Repair failed for video %s: %s", item.id, exc)
                _exclude_item(db, item)
                touched_content_ids.add(int(item.id))
                stats.failed += 1
                stats.excluded += 1

        if touched_content_ids:
            invalidate_tiered_feed_cache()
            refresh_cached_playlist_items(db, content_ids=sorted(touched_content_ids))

        return stats
    finally:
        db.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Repair persisted broken video summaries")
    parser.add_argument(
        "--hours-back",
        type=int,
        default=None,
        help="Optional time window for recently published videos; omit to scan all promoted videos",
    )
    parser.add_argument("--limit", type=int, default=250, help="Maximum matching videos to process")
    parser.add_argument("--dry-run", action="store_true", help="Only count matching videos")
    args = parser.parse_args(argv)

    matches = find_matching_broken_video_summary_ids(
        hours_back=args.hours_back,
        limit=args.limit,
    )

    scope = f"last {args.hours_back}h" if args.hours_back is not None else "all time"
    print(f"Matching broken video summaries ({scope}): {len(matches)}")
    if matches:
        print(f"Sample IDs: {matches[:10]}")
    if args.dry_run:
        return 0

    stats = repair_broken_video_summaries(hours_back=args.hours_back, limit=args.limit)
    print(
        "Repaired broken video summaries: "
        f"matched={stats.matched} scanned={stats.scanned} regenerated={stats.regenerated} "
        f"excluded={stats.excluded} skipped={stats.skipped} failed={stats.failed}"
    )
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
