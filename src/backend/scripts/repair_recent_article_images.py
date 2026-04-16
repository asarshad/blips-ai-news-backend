#!/usr/bin/env python3
"""Repair recent promoted article image backlog with the latest recovery logic."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.services.article_image_service import repair_article_image_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="How many recent days of article rows to scan (default: 7).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Maximum number of candidate rows to inspect (default: 300).",
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Scan candidate rows too; default is promoted-only backlog repair.",
    )
    parser.add_argument(
        "--all-reasons",
        action="store_true",
        help=(
            "Scan any article needing metadata repair; default focuses on image-pending "
            "backlog rows only."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        readiness_reasons = None
        if not args.all_reasons:
            readiness_reasons = (
                "missing_article_image",
                "awaiting_article_image_verification",
            )
        result = repair_article_image_metadata(
            db,
            lookback_days=max(1, int(args.lookback_days)),
            limit=max(1, int(args.limit)),
            include_generic=True,
            promoted_only=not args.all_statuses,
            readiness_reasons=readiness_reasons,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
