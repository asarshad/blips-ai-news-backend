"""Generate a 14-day reel source scorecard from ingestion + content tables."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List, Sequence

from sqlalchemy import case, func

# Make script runnable as `python scripts/...` from backend root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentType
from app.models.ingestion_progress import IngestionProgress


@dataclass(frozen=True)
class ScoreRow:
    feed_name: str
    inserted_total: int
    attempts_total: int
    inserted_per_day: float
    attempts_per_day: float
    conversion: float
    duplicate_suppression_ratio: float
    failed_days: int
    exhausted_days: int
    content_rows: int


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_num(value: float) -> str:
    return f"{value:.2f}"


def _print_table(rows: Iterable[ScoreRow], *, days: int) -> None:
    header = (
        "feed_name",
        "inserted/day",
        "attempts/day",
        "conversion",
        "dup_supp",
        "failed_days",
        "exhausted_days",
        "content_rows",
    )
    print(
        f"Reels Source Scorecard (last {days} days)\n"
        "--------------------------------------------------------------------------"
    )
    print(
        f"{header[0]:40} {header[1]:>12} {header[2]:>12} {header[3]:>10} "
        f"{header[4]:>10} {header[5]:>11} {header[6]:>14} {header[7]:>12}"
    )
    for row in rows:
        print(
            f"{row.feed_name[:40]:40} "
            f"{_fmt_num(row.inserted_per_day):>12} "
            f"{_fmt_num(row.attempts_per_day):>12} "
            f"{_fmt_pct(row.conversion):>10} "
            f"{_fmt_pct(row.duplicate_suppression_ratio):>10} "
            f"{row.failed_days:>11} "
            f"{row.exhausted_days:>14} "
            f"{row.content_rows:>12}"
        )


def build_scorecard(*, days: int, as_of: date | None = None) -> List[ScoreRow]:
    if days <= 0:
        raise ValueError("days must be > 0")

    end_day = as_of or date.today()
    start_day = end_day - timedelta(days=days - 1)

    db = SessionLocal()
    try:
        progress_rows = (
            db.query(
                IngestionProgress.feed_name.label("feed_name"),
                func.sum(IngestionProgress.items_ingested).label("inserted_total"),
                func.sum(IngestionProgress.items_attempted).label("attempts_total"),
                func.sum(case((IngestionProgress.status == "failed", 1), else_=0)).label(
                    "failed_days"
                ),
                func.sum(
                    case(
                        (
                            func.lower(func.coalesce(IngestionProgress.last_error, "")).like(
                                "%exhaust%"
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("exhausted_days"),
            )
            .filter(
                IngestionProgress.source_type == "youtube_reel",
                IngestionProgress.day_utc >= start_day,
                IngestionProgress.day_utc <= end_day,
            )
            .group_by(IngestionProgress.feed_name)
            .all()
        )

        content_counts = {
            source: int(total or 0)
            for source, total in (
                db.query(ContentItem.source, func.count(ContentItem.id))
                .filter(
                    ContentItem.type == ContentType.REEL,
                    ContentItem.ingestion_day >= start_day,
                    ContentItem.ingestion_day <= end_day,
                )
                .group_by(ContentItem.source)
                .all()
            )
        }
    finally:
        db.close()

    rows: List[ScoreRow] = []
    for row in progress_rows:
        feed_name = str(row.feed_name)
        inserted_total = int(row.inserted_total or 0)
        attempts_total = int(row.attempts_total or 0)
        failed_days = int(row.failed_days or 0)
        exhausted_days = int(row.exhausted_days or 0)

        conversion = (inserted_total / attempts_total) if attempts_total > 0 else 0.0
        duplicate_suppression_ratio = (
            max(0, attempts_total - inserted_total) / attempts_total if attempts_total > 0 else 0.0
        )

        rows.append(
            ScoreRow(
                feed_name=feed_name,
                inserted_total=inserted_total,
                attempts_total=attempts_total,
                inserted_per_day=inserted_total / days,
                attempts_per_day=attempts_total / days,
                conversion=conversion,
                duplicate_suppression_ratio=duplicate_suppression_ratio,
                failed_days=failed_days,
                exhausted_days=exhausted_days,
                content_rows=content_counts.get(feed_name, 0),
            )
        )

    rows.sort(key=lambda item: (-item.inserted_per_day, -item.conversion, item.feed_name.lower()))
    return rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reel source performance scorecard.")
    parser.add_argument("--days", type=int, default=14, help="Lookback window in days.")
    parser.add_argument(
        "--as-of",
        default=None,
        help="Inclusive end day in YYYY-MM-DD (defaults to today).",
    )
    return parser.parse_args(argv)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    rows = build_scorecard(days=int(args.days), as_of=_parse_date(args.as_of))
    _print_table(rows, days=int(args.days))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
