#!/usr/bin/env python3
"""Stable operator-run repair/backfill hook.

Replace this module's `run()` implementation whenever we need a new
one-off repair or backfill, while keeping the same admin API endpoint.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.services.article_image_service import repair_article_image_metadata
from app.services.content_event_backfill_service import enqueue_pending_content_events


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
