"""Compare the live video/reel feed with a curated-weekly challenger ranking."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

# Make script runnable as `python scripts/...` from backend root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.video_strategy_validation import (
    ValidationItem,
    challenger_rank,
    summarize_ranked,
)

_SURFACE_TYPE = {"videos": "VIDEO", "reels": "REEL"}


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _fetch_json(
    session: requests.Session,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    response = session.get(url, headers=headers or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _to_item(detail: Dict[str, Any], surface: str) -> ValidationItem:
    return ValidationItem(
        id=int(detail["id"]),
        surface=surface,
        title=str(detail.get("title") or ""),
        source=str(detail.get("source") or ""),
        source_url=str(detail.get("source_url") or ""),
        published_at=_parse_datetime(detail.get("published_at")),
        description=str(detail.get("description") or ""),
        summary=str(detail.get("summary") or ""),
        topics=detail.get("topics") or [],
        entities=detail.get("entities") or [],
        quality_score=detail.get("quality_score"),
        trend_score=detail.get("trend_score"),
        global_score=detail.get("global_score"),
        editorial_boost=int(detail.get("editorial_boost") or 0),
    )


def _fetch_public_feed(
    session: requests.Session,
    *,
    base_url: str,
    surface: str,
    limit: int,
) -> List[Dict[str, Any]]:
    path = "/api/v1/videos/recent" if surface == "videos" else "/api/v1/videos/reels"
    payload = _fetch_json(session, f"{base_url.rstrip('/')}{path}?limit={limit}")
    return list(payload.get("items") or [])


def _fetch_pool_summaries(
    session: requests.Session,
    *,
    base_url: str,
    admin_key: str,
    surface: str,
    days: int,
    page_size: int,
    max_pages: int,
) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    summaries: List[Dict[str, Any]] = []
    headers = {"X-Admin-Key": admin_key}

    for page in range(1, max_pages + 1):
        payload = _fetch_json(
            session,
            (
                f"{base_url.rstrip('/')}/api/v1/admin/editorial/content"
                f"?type={_SURFACE_TYPE[surface]}&page={page}&page_size={page_size}"
            ),
            headers=headers,
        )
        items = list(payload.get("items") or [])
        if not items:
            break

        keep_any = False
        for item in items:
            published_at = _parse_datetime(item.get("published_at"))
            if published_at is None or published_at >= cutoff:
                summaries.append(item)
                keep_any = True

        oldest = _parse_datetime(items[-1].get("published_at"))
        if oldest is not None and oldest < cutoff and not keep_any:
            break

    deduped: Dict[int, Dict[str, Any]] = {}
    for item in summaries:
        deduped[int(item["id"])] = item
    return list(deduped.values())


def _fetch_details(
    session: requests.Session,
    *,
    base_url: str,
    admin_key: str,
    ids: Iterable[int],
    surface: str,
    max_workers: int,
) -> List[ValidationItem]:
    headers = {"X-Admin-Key": admin_key}
    items: List[ValidationItem] = []

    def _one(content_id: int) -> ValidationItem:
        detail = _fetch_json(
            session,
            f"{base_url.rstrip('/')}/api/v1/admin/editorial/content/{content_id}",
            headers=headers,
        )
        return _to_item(detail, surface)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_one, int(content_id)): int(content_id) for content_id in ids}
        for future in as_completed(futures):
            items.append(future.result())

    return items


def _compare_lists(
    current: Sequence[ValidationItem],
    challenger: Sequence[ValidationItem],
) -> Dict[str, Any]:
    current_ids = {item.id for item in current}
    challenger_ids = {item.id for item in challenger}
    added = [item for item in challenger if item.id not in current_ids]
    removed = [item for item in current if item.id not in challenger_ids]
    return {
        "overlap_count": len(current_ids & challenger_ids),
        "added_count": len(added),
        "removed_count": len(removed),
        "added_titles": [item.title for item in added[:10]],
        "removed_titles": [item.title for item in removed[:10]],
    }


def _run_surface(
    session: requests.Session,
    *,
    base_url: str,
    admin_key: str,
    surface: str,
    days: int,
    limit: int,
    page_size: int,
    max_pages: int,
    max_workers: int,
) -> Dict[str, Any]:
    feed_payload = _fetch_public_feed(session, base_url=base_url, surface=surface, limit=limit)
    feed_ids = [int(item["id"]) for item in feed_payload if item.get("id") is not None]
    pool_summaries = _fetch_pool_summaries(
        session,
        base_url=base_url,
        admin_key=admin_key,
        surface=surface,
        days=days,
        page_size=page_size,
        max_pages=max_pages,
    )
    pool_ids = [int(item["id"]) for item in pool_summaries if item.get("id") is not None]
    missing_feed_ids = [item_id for item_id in feed_ids if item_id not in set(pool_ids)]
    detail_ids = list(dict.fromkeys(pool_ids + missing_feed_ids))
    detailed_items = _fetch_details(
        session,
        base_url=base_url,
        admin_key=admin_key,
        ids=detail_ids,
        surface=surface,
        max_workers=max_workers,
    )
    by_id = {item.id: item for item in detailed_items}

    pool_items = [by_id[item_id] for item_id in pool_ids if item_id in by_id]
    live_items = [by_id[item_id] for item_id in feed_ids if item_id in by_id]

    challenger_ranked, excluded = challenger_rank(pool_items, surface=surface, limit=limit)
    challenger_items = [ranked.item for ranked in challenger_ranked]

    return {
        "surface": surface,
        "window_days": days,
        "pool_count": len(pool_items),
        "live_count": len(live_items),
        "challenger_count": len(challenger_items),
        "excluded_reasons": excluded,
        "current_metrics": summarize_ranked(live_items[:limit]),
        "challenger_metrics": summarize_ranked(challenger_items),
        "comparison": _compare_lists(live_items[:limit], challenger_items),
        "challenger_top_titles": [item.title for item in challenger_items[:10]],
        "current_top_titles": [item.title for item in live_items[:10]],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest a curated-weekly challenger ranking against the live feed."
    )
    parser.add_argument(
        "--base-url",
        default="https://blips-api.onrender.com",
        help="Base URL for the deployed API.",
    )
    parser.add_argument(
        "--admin-key",
        default=os.getenv("BLIPS_ADMIN_KEY") or os.getenv("ADMIN_KEY"),
        help="Admin API key used for editorial endpoints.",
    )
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days.")
    parser.add_argument("--limit", type=int, default=50, help="Top-N list size to compare.")
    parser.add_argument("--page-size", type=int, default=200, help="Admin content page size.")
    parser.add_argument("--max-pages", type=int, default=10, help="Max admin content pages.")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="Max concurrent detail requests.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to save the JSON report.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.admin_key:
        raise SystemExit("--admin-key is required")

    session = requests.Session()
    report = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "days": int(args.days),
        "limit": int(args.limit),
        "surfaces": {
            surface: _run_surface(
                session,
                base_url=args.base_url,
                admin_key=str(args.admin_key),
                surface=surface,
                days=int(args.days),
                limit=int(args.limit),
                page_size=int(args.page_size),
                max_pages=int(args.max_pages),
                max_workers=int(args.max_workers),
            )
            for surface in ("videos", "reels")
        },
    }

    serialized = json.dumps(report, indent=2, sort_keys=True)
    print(serialized)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
