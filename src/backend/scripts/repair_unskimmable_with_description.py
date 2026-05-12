#!/usr/bin/env python3
"""Re-queue articles that were terminally rejected as unskimmable but have a usable RSS description.

Targets items where:
  - tech_relevance_reason starts with 'article_unskimmable_terminal:max_unskimmable_attempts'
  - is_suppressed = True
  - curation_status = CANDIDATE
  - description word count >= MIN_DESCRIPTION_WORDS

Resets them to a re-processable state so the content_ai_service description-fallback
path can generate a summary from the RSS description instead of permanently suppressing.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.content_readiness import seed_content_readiness

_TERMINAL_REASON_PREFIX = "article_unskimmable_terminal:max_unskimmable_attempts"
_DEFAULT_MIN_DESC_WORDS = 20
_DEFAULT_LOOKBACK_DAYS = 90


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-desc-words",
        type=int,
        default=_DEFAULT_MIN_DESC_WORDS,
        help=f"Minimum description word count to qualify (default: {_DEFAULT_MIN_DESC_WORDS}).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=_DEFAULT_LOOKBACK_DAYS,
        help=f"How far back to scan in days (default: {_DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Limit to a specific source name (e.g. 'CNBC Technology'). Default: all sources.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without making any DB changes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of items to repair in one run (default: 500).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = SessionLocal()
    try:
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(days=args.lookback_days)

        query = db.query(ContentItem).filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.is_suppressed.is_(True),
            ContentItem.curation_status == ContentStatus.CANDIDATE,
            ContentItem.tech_relevance_reason.like(f"{_TERMINAL_REASON_PREFIX}%"),
            ContentItem.published_at >= cutoff,
        )
        if args.source:
            query = query.filter(ContentItem.source == args.source)

        candidates = query.order_by(ContentItem.published_at.desc()).limit(args.limit * 5).all()

        qualified = [
            item
            for item in candidates
            if len((item.description or "").strip().split()) >= args.min_desc_words
        ]

        print(
            f"Scanned: {len(candidates)}  |  Qualified (desc >= {args.min_desc_words} words): {len(qualified)}"
        )

        if args.dry_run:
            print("\n[DRY RUN — no changes made]\n")
            for item in qualified[: args.limit]:
                desc_words = len((item.description or "").strip().split())
                print(
                    f"  id={item.id:>7}  source={item.source:<25}  "
                    f"desc_words={desc_words:<4}  title={item.title[:70] if item.title else ''}"
                )
            return

        repaired = 0
        for item in qualified[: args.limit]:
            item.is_suppressed = False
            item.ai_processed = False
            item.summary = None
            item.promotion_score = None
            item.tech_relevance_reason = None
            item.promotion_reason = None
            item.updated_at = datetime.utcnow()
            seed_content_readiness(item)
            repaired += 1

        db.commit()
        print(f"Repaired {repaired} items — they will be re-queued on the next promotion cycle.")

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
