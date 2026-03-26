#!/usr/bin/env python3
"""
Regenerate summaries for recent promoted videos.

Usage:
    python scripts/regenerate_recent_video_summaries.py --hours-back 72
    python scripts/regenerate_recent_video_summaries.py --hours-back 72 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.integrations.llm_client import (
    LLMClient,
    is_video_summary_acceptable,
    normalize_video_summary_output,
)
from app.models.content import ContentItem, ContentStatus, ContentType
from app.repositories.content_repo import ContentItemRepository

logger = get_logger(__name__)


@dataclass
class RegenerationStats:
    scanned: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0


def regenerate_recent_video_summaries(*, hours_back: int = 72, limit: int = 250) -> RegenerationStats:
    """Re-run video summarization for recent promoted videos."""
    db = SessionLocal()
    stats = RegenerationStats()

    try:
        llm_client = LLMClient()
        if not llm_client.is_configured():
            raise RuntimeError(f"{llm_client.get_provider()} API key is not configured")

        repo = ContentItemRepository(db)
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
            .all()
        )

        for item in items:
            stats.scanned += 1
            text = item.content_text or item.description or item.title
            if not (text or "").strip():
                logger.info("Skipping video %s: no usable text", item.id)
                stats.skipped += 1
                continue

            try:
                result = llm_client.summarize_video(item.title, text)
                summary = normalize_video_summary_output(result.summary)
                if not is_video_summary_acceptable(summary):
                    logger.info("Skipping video %s: summary below minimum word target", item.id)
                    stats.skipped += 1
                    continue

                repo.mark_ai_processed(item.id, summary=summary, topics=item.topics or [])
                if result.conversation_starters:
                    item.conversation_starters = result.conversation_starters
                    db.commit()
                stats.processed += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("Failed to regenerate video summary for %s: %s", item.id, exc)
                stats.failed += 1

        return stats
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate recent promoted video summaries")
    parser.add_argument("--hours-back", type=int, default=72, help="How far back to target videos")
    parser.add_argument("--limit", type=int, default=250, help="Maximum videos to process")
    parser.add_argument("--dry-run", action="store_true", help="Only count matching videos")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=args.hours_back)
        count = (
            db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .count()
        )
    finally:
        db.close()

    print(f"Matching promoted videos in the last {args.hours_back}h: {count}")
    if args.dry_run:
        return 0

    stats = regenerate_recent_video_summaries(hours_back=args.hours_back, limit=args.limit)
    print(
        "Processed recent video summaries: "
        f"scanned={stats.scanned} processed={stats.processed} "
        f"skipped={stats.skipped} failed={stats.failed}"
    )
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
