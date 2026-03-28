"""Measure LLM recovery hit rate on promoted article rows that still lack images."""

from __future__ import annotations

import argparse
import json

from app.db.base import SessionLocal
from app.services.article_image_service import evaluate_llm_article_image_recovery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Optional published_at lookback window. Omit to scan all current null-image articles.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of rows to inspect. Use 0 to scan all matching rows.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist recovered image URLs and refresh article readiness.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=20,
        help="How many success/failure examples to include in the report.",
    )
    return parser.parse_args()

def main() -> int:
    args = parse_args()

    db = SessionLocal()
    try:
        result = evaluate_llm_article_image_recovery(
            db,
            lookback_days=args.lookback_days,
            limit=args.limit,
            sample_size=args.sample_size,
            apply=args.apply,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
