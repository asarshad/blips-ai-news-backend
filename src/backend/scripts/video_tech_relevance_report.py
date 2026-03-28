"""Daily scorecard for video tech-relevance classifier impact."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import func

# Make script runnable as `python scripts/...` from backend root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.promotion_service import (
    PromotionService,
    _channel_config_for_item,
    classify_promotion_block,
)


LLM_BLOCK_PREFIX = "llm_"


@dataclass(frozen=True)
class DayRow:
    day: date
    total_rows: int
    classifier_rows: int
    promoted_rows: int
    broad_news_rows: int
    none_rows: int
    incidental_rows: int
    meaningful_rows: int
    primary_rows: int
    mixed_roundup_rows: int
    llm_block_rows: int
    promoted_llm_block_rows: int


@dataclass(frozen=True)
class SourceRow:
    source: str
    total_rows: int
    classifier_rows: int
    llm_block_rows: int
    promoted_llm_block_rows: int
    mixed_roundup_rows: int


def _fmt_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _decision_day(item: ContentItem) -> date:
    if getattr(item, "ingestion_day", None):
        return item.ingestion_day
    created_at = getattr(item, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.date()
    published_at = getattr(item, "published_at", None)
    if isinstance(published_at, datetime):
        return published_at.date()
    return date.today()


def _is_classifier_covered(item: ContentItem) -> bool:
    return bool(getattr(item, "tech_relevance", None) or getattr(item, "is_mixed_roundup", None))


def build_report(*, days: int, as_of: date | None = None) -> tuple[list[DayRow], list[SourceRow]]:
    if days <= 0:
        raise ValueError("days must be > 0")

    end_day = as_of or date.today()
    start_day = end_day - timedelta(days=days - 1)

    db = SessionLocal()
    try:
        svc = PromotionService(db)
        story_topic_counts, story_entity_counts = svc._get_recent_story_context()

        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
                func.coalesce(
                    ContentItem.ingestion_day,
                    func.date(ContentItem.created_at),
                )
                >= start_day,
                func.coalesce(
                    ContentItem.ingestion_day,
                    func.date(ContentItem.created_at),
                )
                <= end_day,
            )
            .order_by(ContentItem.created_at.desc())
            .all()
        )
        source_profiles = svc._get_source_profiles(items)
    finally:
        db.close()

    day_stats: dict[date, Counter] = defaultdict(Counter)
    source_stats: dict[str, Counter] = defaultdict(Counter)

    for item in items:
        decision_day = _decision_day(item)
        channel_config = _channel_config_for_item(item)
        source_profile = source_profiles.get(getattr(item, "channel_id", None) or "")
        block_reason = classify_promotion_block(
            item,
            item.type,
            story_topic_counts=story_topic_counts,
            story_entity_counts=story_entity_counts,
            channel_config=channel_config,
            source_profile=source_profile,
        )

        for stats in (day_stats[decision_day], source_stats[item.source or "Unknown"]):
            stats["total_rows"] += 1
            if _is_classifier_covered(item):
                stats["classifier_rows"] += 1
            if getattr(item, "curation_status", None) == ContentStatus.PROMOTED:
                stats["promoted_rows"] += 1
            if channel_config and getattr(channel_config, "role", None):
                if str(channel_config.role.value) == "news":
                    stats["broad_news_rows"] += 1
            tech_relevance = str(getattr(item, "tech_relevance", "") or "").lower()
            if tech_relevance in {"none", "incidental", "meaningful", "primary"}:
                stats[f"{tech_relevance}_rows"] += 1
            if getattr(item, "is_mixed_roundup", None) is True:
                stats["mixed_roundup_rows"] += 1
            if isinstance(block_reason, str) and block_reason.startswith(LLM_BLOCK_PREFIX):
                stats["llm_block_rows"] += 1
                if getattr(item, "curation_status", None) == ContentStatus.PROMOTED:
                    stats["promoted_llm_block_rows"] += 1

    day_rows = [
        DayRow(
            day=day,
            total_rows=stats["total_rows"],
            classifier_rows=stats["classifier_rows"],
            promoted_rows=stats["promoted_rows"],
            broad_news_rows=stats["broad_news_rows"],
            none_rows=stats["none_rows"],
            incidental_rows=stats["incidental_rows"],
            meaningful_rows=stats["meaningful_rows"],
            primary_rows=stats["primary_rows"],
            mixed_roundup_rows=stats["mixed_roundup_rows"],
            llm_block_rows=stats["llm_block_rows"],
            promoted_llm_block_rows=stats["promoted_llm_block_rows"],
        )
        for day, stats in sorted(day_stats.items())
    ]

    source_rows = [
        SourceRow(
            source=source,
            total_rows=stats["total_rows"],
            classifier_rows=stats["classifier_rows"],
            llm_block_rows=stats["llm_block_rows"],
            promoted_llm_block_rows=stats["promoted_llm_block_rows"],
            mixed_roundup_rows=stats["mixed_roundup_rows"],
        )
        for source, stats in source_stats.items()
        if stats["llm_block_rows"] > 0 or stats["mixed_roundup_rows"] > 0
    ]
    source_rows.sort(
        key=lambda row: (-row.llm_block_rows, -row.promoted_llm_block_rows, row.source.lower())
    )
    return day_rows, source_rows


def _print_day_rows(rows: Iterable[DayRow]) -> None:
    print("Video Tech Relevance Daily Report")
    print(
        "--------------------------------------------------------------------------------------------------------------"
    )
    print(
        f"{'day':10} {'rows':>6} {'covered':>8} {'promoted':>9} {'broad':>7} "
        f"{'none':>6} {'incid':>6} {'meaning':>8} {'primary':>8} "
        f"{'roundup':>8} {'llm_blk':>8} {'prom_llm':>9}"
    )
    for row in rows:
        print(
            f"{row.day.isoformat():10} "
            f"{row.total_rows:>6} "
            f"{row.classifier_rows:>8} "
            f"{row.promoted_rows:>9} "
            f"{row.broad_news_rows:>7} "
            f"{row.none_rows:>6} "
            f"{row.incidental_rows:>6} "
            f"{row.meaningful_rows:>8} "
            f"{row.primary_rows:>8} "
            f"{row.mixed_roundup_rows:>8} "
            f"{row.llm_block_rows:>8} "
            f"{row.promoted_llm_block_rows:>9}"
        )


def _print_source_rows(rows: Iterable[SourceRow], *, limit: int) -> None:
    print("\nTop impacted sources")
    print("--------------------------------------------------------------------------------")
    print(
        f"{'source':35} {'rows':>6} {'covered':>8} {'llm_blk':>8} {'prom_llm':>9} {'roundup':>8}"
    )
    for row in list(rows)[:limit]:
        print(
            f"{row.source[:35]:35} "
            f"{row.total_rows:>6} "
            f"{row.classifier_rows:>8} "
            f"{row.llm_block_rows:>8} "
            f"{row.promoted_llm_block_rows:>9} "
            f"{row.mixed_roundup_rows:>8}"
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report daily video tech-classifier impact.")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days.")
    parser.add_argument(
        "--as-of",
        default=None,
        help="Inclusive end day in YYYY-MM-DD (defaults to today).",
    )
    parser.add_argument(
        "--source-limit",
        type=int,
        default=10,
        help="How many sources to show in the impacted-sources table.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    day_rows, source_rows = build_report(days=int(args.days), as_of=_parse_date(args.as_of))
    _print_day_rows(day_rows)
    _print_source_rows(source_rows, limit=max(1, int(args.source_limit)))

    total_rows = sum(row.total_rows for row in day_rows)
    classifier_rows = sum(row.classifier_rows for row in day_rows)
    llm_blocks = sum(row.llm_block_rows for row in day_rows)
    promoted_llm_blocks = sum(row.promoted_llm_block_rows for row in day_rows)
    print("\nSummary")
    print("-------")
    print(f"Classifier coverage: {classifier_rows}/{total_rows} ({_fmt_pct(classifier_rows, total_rows)})")
    print(f"LLM-driven broad-news blocks: {llm_blocks}/{total_rows} ({_fmt_pct(llm_blocks, total_rows)})")
    print(
        "Already-promoted items that would now be blocked: "
        f"{promoted_llm_blocks}/{total_rows} ({_fmt_pct(promoted_llm_blocks, total_rows)})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
