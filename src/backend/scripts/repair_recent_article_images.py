"""Repair recent article image metadata for missing, generic, or suspicious rows."""

from __future__ import annotations

import argparse
import json

from app.db.base import SessionLocal
from app.services.article_image_service import repair_article_image_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=3,
        help="How many recent days of article rows to scan (default: 3).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of candidate rows to inspect (default: 500).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        result = repair_article_image_metadata(
            db,
            lookback_days=max(1, int(args.lookback_days)),
            limit=max(1, int(args.limit)),
            include_generic=True,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
