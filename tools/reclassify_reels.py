#!/usr/bin/env python3
"""
Reel Reclassification Script
=============================
Finds content items classified as REEL that have duration_seconds > REEL_MAX_DURATION_SECONDS
and reclassifies them as VIDEO.

This is a one-time data cleanup after tightening ingestion classification.
Safe to re-run (idempotent).

Usage:
    # Dry run (default) — shows what WOULD change
    python tools/reclassify_reels.py

    # Apply changes
    python tools/reclassify_reels.py --apply
"""

import argparse
import os
import sys

# Ensure the src/backend directory is on the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))

from app.core.config import settings
from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentType


def main():
    parser = argparse.ArgumentParser(description="Reclassify oversized reels as videos")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply the changes (default is dry-run)",
    )
    args = parser.parse_args()

    max_dur = settings.REEL_MAX_DURATION_SECONDS
    print(f"REEL_MAX_DURATION_SECONDS = {max_dur}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print()

    db = SessionLocal()
    try:
        # Find misclassified reels: type=REEL and duration > max
        oversized = (
            db.query(ContentItem)
            .filter(
                ContentItem.type == ContentType.REEL,
                ContentItem.is_suppressed.is_(False),
                ContentItem.duration_seconds.isnot(None),
                ContentItem.duration_seconds > max_dur,
            )
            .all()
        )

        print(f"Found {len(oversized)} REELs with duration > {max_dur}s:")
        for item in oversized:
            print(
                f"  id={item.id}  dur={item.duration_seconds}s  "
                f"title={item.title[:60]}  url={item.source_url}"
            )

        if not oversized:
            print("\nNothing to reclassify.")
            return

        if args.apply:
            for item in oversized:
                item.type = ContentType.VIDEO
            db.commit()
            print(f"\n✓ Reclassified {len(oversized)} items from REEL → VIDEO.")
        else:
            print(f"\nDry run complete. Use --apply to reclassify {len(oversized)} items.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
