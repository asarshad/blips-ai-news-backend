#!/usr/bin/env python3
"""
Backfill persisted Shorts rows from VIDEO to REEL.

Use this after deploying the session-playlist surface-rule fix so old rows stop
duplicating across the Videos and Reels surfaces.

Examples:
    python scripts/backfill_shorts_to_reels.py --dry-run
    python scripts/backfill_shorts_to_reels.py
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from sqlalchemy import func, or_

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentType


def _shorts_clause():
    return or_(
        ContentItem.video_url.ilike("%youtube.com/shorts/%"),
        ContentItem.source_url.ilike("%youtube.com/shorts/%"),
        ContentItem.canonical_url.ilike("%youtube.com/shorts/%"),
    )


def run(*, dry_run: bool) -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        duration_ok = or_(
            ContentItem.duration_seconds.is_(None),
            ContentItem.duration_seconds <= settings.REEL_MAX_DURATION_SECONDS,
        )
        query = db.query(ContentItem).filter(
            ContentItem.type == ContentType.VIDEO,
            _shorts_clause(),
            duration_ok,
        )

        total = int(query.with_entities(func.count(ContentItem.id)).scalar() or 0)
        print(f"matching_rows={total}")
        if total == 0:
            return 0

        sample = (
            query.with_entities(
                ContentItem.id,
                ContentItem.title,
                ContentItem.source_url,
                ContentItem.duration_seconds,
            )
            .order_by(ContentItem.id.asc())
            .limit(10)
            .all()
        )
        for row in sample:
            print(
                f"sample id={row.id} duration={row.duration_seconds} "
                f"title={row.title!r} url={row.source_url}"
            )

        if dry_run:
            print("dry_run=true; no rows updated")
            return total

        updated = query.update(
            {
                ContentItem.type: ContentType.REEL,
            },
            synchronize_session=False,
        )
        db.commit()
        print(f"updated_rows={int(updated)}")
        return int(updated)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill persisted Shorts rows from VIDEO to REEL."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show matching rows without updating."
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    run(dry_run=args.dry_run)
