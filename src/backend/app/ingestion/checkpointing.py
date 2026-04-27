"""Checkpointed ingestion runner.

This module is a thin public facade to keep imports stable.

Implementation details live in smaller modules:
- app.ingestion.checkpoint_defaults
- app.ingestion.checkpoint_worker
- app.ingestion.checkpoint_loop
"""

import os
import re
import signal
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.ingestion.checkpoint_defaults import FeedDefault
from app.ingestion.checkpoint_defaults import build_defaults as _build_defaults
from app.ingestion.checkpoint_defaults import owner_token as _owner_token
from app.ingestion.checkpoint_loop import run_checkpoint_loop as _run_checkpoint_loop
from app.ingestion.checkpoint_worker import (
    process_progress_row_batch as _process_progress_row_batch,
)
from app.ingestion.time import get_ingestion_day, get_ingestion_day_bounds
from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.repositories.content_repo import ContentItemRepository
from app.repositories.ingestion_budget_repo import IngestionBudgetRepository
from app.repositories.ingestion_progress_repo import IngestionProgressRepository
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.inventory_service import invalidate_health_cache
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

logger = get_logger(__name__)

STOP_EVENT = threading.Event()
_SOURCE_KEY_RE = re.compile(r"[^a-z0-9]+")


def _source_key(value: str | None) -> str:
    return _SOURCE_KEY_RE.sub("", (value or "").lower())


def _sum_targets(rows, *, source_type: str, excluded_feed_names: set[str] | None = None) -> int:
    excluded = excluded_feed_names or set()
    return sum(
        int(row.target or 0)
        for row in rows
        if row.source_type == source_type and str(row.feed_name) not in excluded
    )


def _bounded_fraction(value: float, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _low_yield_rss_source_names(
    db: Session,
    *,
    day_utc,
    feed_names: list[str],
) -> set[str]:
    """Return RSS feed names that are currently producing poor ready yield.

    The guard is intentionally conservative: it only trips when a source has
    enough promoted items to be statistically meaningful, poor ready yield, and
    a high share of unskimmable promoted articles. It pauses work for the
    current run only; later runs can resume once readiness catches up.
    """
    if not settings.ARTICLE_RSS_READY_YIELD_GUARD_ENABLED or not feed_names:
        return set()

    try:
        feed_by_key = {_source_key(name): name for name in feed_names}
        start, end = get_ingestion_day_bounds(day=day_utc)
        day_filter = or_(
            ContentItem.ingestion_day == day_utc,
            and_(
                ContentItem.ingestion_day.is_(None),
                ContentItem.created_at >= start,
                ContentItem.created_at < end,
            ),
        )
        rows = (
            db.query(
                ContentItem.source,
                ContentItem.readiness_status,
                ContentItem.readiness_reason,
            )
            .filter(
                day_filter,
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
            )
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Article RSS yield guard skipped after metrics lookup failed: %s", exc)
        return set()

    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"promoted": 0, "ready": 0, "unskimmable": 0}
    )
    for source, readiness_status, readiness_reason in rows:
        key = _source_key(str(source or ""))
        if key not in feed_by_key:
            continue
        bucket = stats[key]
        bucket["promoted"] += 1
        if str(readiness_status or "") == ContentReadinessStatus.READY.value:
            bucket["ready"] += 1
        if str(readiness_reason or "") == "article_unskimmable_retry":
            bucket["unskimmable"] += 1

    min_promoted = max(1, int(settings.ARTICLE_RSS_READY_YIELD_MIN_PROMOTED))
    max_ready_ratio = _bounded_fraction(
        settings.ARTICLE_RSS_READY_YIELD_MAX_READY_RATIO,
        default=0.35,
    )
    min_unskimmable_ratio = _bounded_fraction(
        settings.ARTICLE_RSS_READY_YIELD_MIN_UNSKIMMABLE_RATIO,
        default=0.6,
    )
    candidates: list[tuple[float, str, dict[str, int]]] = []
    for key, bucket in stats.items():
        promoted = int(bucket["promoted"])
        if promoted < min_promoted:
            continue
        ready_ratio = int(bucket["ready"]) / promoted
        unskimmable_ratio = int(bucket["unskimmable"]) / promoted
        if ready_ratio <= max_ready_ratio and unskimmable_ratio >= min_unskimmable_ratio:
            severity = unskimmable_ratio - ready_ratio
            candidates.append((severity, feed_by_key[key], bucket))

    if not candidates:
        return set()

    max_pause_fraction = _bounded_fraction(
        settings.ARTICLE_RSS_READY_YIELD_MAX_PAUSE_FRACTION,
        default=0.35,
    )
    max_paused = max(1, int(len(feed_names) * max_pause_fraction))
    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:max_paused]
    paused = {feed_name for _severity, feed_name, _bucket in selected}
    logger.warning(
        "Article RSS ready-yield guard pausing %s/%s sources for %s: %s",
        len(paused),
        len(feed_names),
        day_utc.isoformat(),
        [
            {
                "source": feed_name,
                "promoted": bucket["promoted"],
                "ready": bucket["ready"],
                "unskimmable": bucket["unskimmable"],
            }
            for _severity, feed_name, bucket in selected
        ],
    )
    return paused


def _bootstrap_target(source_type: str, *, daily_cap: int, reel_cap: int) -> int:
    if source_type == "youtube_reel":
        floor = int(os.getenv("YT_BOOTSTRAP_REEL_TARGET_MIN", "2"))
        return max(floor, int(reel_cap or 0))
    floor = int(os.getenv("YT_BOOTSTRAP_VIDEO_TARGET_MIN", "3"))
    return max(floor, int(daily_cap or 0))


def _prime_bootstrap_youtube_rows(
    *,
    db: Session,
    repo: IngestionProgressRepository,
    day_utc,
    process_batch,
    default_batch_size: int,
) -> List[Dict[str, object]]:
    """Force a one-time latest-first pass for newly added curated channels."""
    if os.getenv("YT_CHANNEL_BOOTSTRAP_ENABLED", "true").lower() not in ("true", "1", "yes", "on"):
        return []

    from app.integrations.youtube_channels import get_bootstrap_channels

    configs = [cfg for cfg in get_bootstrap_channels() if cfg.enabled]
    if not configs:
        return []

    now = datetime.utcnow()
    max_profile_age_days = int(os.getenv("YT_BOOTSTRAP_MAX_PROFILE_AGE_DAYS", "2"))
    max_rows = int(os.getenv("YT_BOOTSTRAP_MAX_ROWS", "32"))
    batch_size = max(default_batch_size, int(os.getenv("YT_BOOTSTRAP_BATCH_SIZE", "10")))

    profile_repo = VideoSourceProfileRepository(db)
    profiles = {
        profile.channel_id: profile for profile in profile_repo.upsert_from_registry(configs)
    }

    bootstrap_rows = []
    for cfg in configs:
        profile = profiles.get(cfg.channel_id)
        if profile is not None and profile.created_at < now - timedelta(days=max_profile_age_days):
            continue

        video_row = repo.get(day_utc=day_utc, source_type="youtube_video", feed_name=cfg.name)
        if (
            video_row is not None
            and int(video_row.items_ingested or 0) == 0
            and not video_row.last_item_cursor
        ):
            bootstrap_rows.append(
                (
                    "youtube_video",
                    cfg.name,
                    _bootstrap_target(
                        "youtube_video",
                        daily_cap=cfg.daily_cap,
                        reel_cap=cfg.effective_daily_reel_cap,
                    ),
                )
            )

        if cfg.effective_daily_reel_cap <= 0:
            continue
        reel_row = repo.get(day_utc=day_utc, source_type="youtube_reel", feed_name=cfg.name)
        if (
            reel_row is not None
            and int(reel_row.items_ingested or 0) == 0
            and not reel_row.last_item_cursor
        ):
            bootstrap_rows.append(
                (
                    "youtube_reel",
                    cfg.name,
                    _bootstrap_target(
                        "youtube_reel",
                        daily_cap=cfg.daily_cap,
                        reel_cap=cfg.effective_daily_reel_cap,
                    ),
                )
            )

    if not bootstrap_rows:
        return []

    primed_ids = repo.prime_youtube_rows(day_utc=day_utc, rows=bootstrap_rows)
    if not primed_ids:
        return []

    results: List[Dict[str, object]] = []
    for row_id in primed_ids[:max_rows]:
        results.append(process_batch(int(row_id), batch_size))

    inserted = sum(int(result.get("inserted", 0) or 0) for result in results)
    attempted = sum(int(result.get("attempted", 0) or 0) for result in results)
    logger.info(
        "Bootstrap processed %s YouTube rows for %s (attempted=%s inserted=%s)",
        min(len(primed_ids), max_rows),
        day_utc.isoformat(),
        attempted,
        inserted,
    )
    if inserted > 0:
        invalidate_health_cache()
        invalidate_tiered_feed_cache()
    return results


def install_signal_handlers() -> None:
    """Install best-effort signal handlers for graceful shutdown."""

    def _handle(_signum, _frame):
        STOP_EVENT.set()

    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
    except Exception:
        # Not available in some runtimes / threads.
        return


def run_checkpointed_ingestion(
    db: Session,
    *,
    redis_client=None,
) -> Dict[str, object]:
    """Run checkpointed ingestion until targets are met (within budget)."""

    if os.getenv("INGESTION_ENABLED", "true").lower() not in ("true", "1", "yes", "on"):
        return {"status": "disabled"}

    if os.getenv("INGESTION_CRON_DISABLED", "false").lower() in ("true", "1", "yes", "on"):
        return {"status": "cron_disabled"}

    day = get_ingestion_day()
    repo = IngestionProgressRepository(db)
    budget_repo = IngestionBudgetRepository(db)

    defaults = _build_defaults(db=db, day_utc=day)
    defaults_tuples = [(d.source_type, d.feed_name, d.target) for d in defaults]
    created = repo.ensure_rows(day_utc=day, defaults=defaults_tuples)
    if created:
        logger.info("Created %s ingestion_progress rows for %s", created, day.isoformat())

    content_repo = ContentItemRepository(db)
    article_supply = content_repo.get_article_supply_counts_on_ingestion_day(day)
    article_target_pending = article_supply["ready"] < settings.DAILY_TARGET_ARTICLES
    rss_feed_names = [feed_name for source_type, feed_name, _target in defaults_tuples if source_type == "rss"]
    low_yield_rss_sources = (
        _low_yield_rss_source_names(db, day_utc=day, feed_names=rss_feed_names)
        if article_target_pending
        else set()
    )

    youtube_defaults = [d for d in defaults_tuples if d[0] != "rss"]
    reopened = repo.reopen_continuous_rows(day_utc=day, defaults=youtube_defaults)
    if reopened:
        logger.info("Reopened %s YouTube progress rows for %s", reopened, day.isoformat())

    if article_target_pending:
        rss_defaults = [
            d
            for d in defaults_tuples
            if d[0] == "rss" and d[1] not in low_yield_rss_sources
        ]
        reopened_rss = repo.reopen_continuous_rows(day_utc=day, defaults=rss_defaults)
        if reopened_rss:
            logger.info(
                "Reopened %s RSS progress rows for %s while article ready supply remained below target (%s/%s ready, %s promoted)",
                reopened_rss,
                day.isoformat(),
                article_supply["ready"],
                settings.DAILY_TARGET_ARTICLES,
                article_supply["promoted"],
            )

    # Ensure strict per-type budgets from configured per-feed targets.
    # Videos/reels use a high multiplier so the budget never blocks continuous
    # ingestion (budget table kept for Phase A concurrency safety; daily cap
    # enforcement is removed).
    progress_rows = repo.list_for_day(day_utc=day)
    article_target = _sum_targets(
        progress_rows,
        source_type="rss",
        excluded_feed_names=low_yield_rss_sources,
    )
    if article_target <= 0:
        article_target = sum(
            d.target
            for d in defaults
            if d.source_type == "rss" and d.feed_name not in low_yield_rss_sources
        )
    video_target = sum(d.target for d in defaults if d.source_type == "youtube_video")
    reel_target = sum(d.target for d in defaults if d.source_type == "youtube_reel")
    budget_repo.ensure(day=day, content_type=ContentType.ARTICLE, target=article_target)
    budget_repo.ensure(day=day, content_type=ContentType.VIDEO, target=video_target * 10)
    budget_repo.ensure(day=day, content_type=ContentType.REEL, target=reel_target * 10)

    logger.info(
        "Article ingestion target for %s: ready=%s promoted=%s desired_ready=%s rss_budget_target=%s",
        day.isoformat(),
        article_supply["ready"],
        article_supply["promoted"],
        settings.DAILY_TARGET_ARTICLES,
        article_target,
    )
    low_yield_rss_row_ids = {
        int(row.id)
        for row in progress_rows
        if row.source_type == "rss" and row.feed_name in low_yield_rss_sources
    }

    owner = _owner_token()
    ttl_ms = int(os.getenv("INGESTION_LEASE_TTL_MS", "60000"))

    ingest_until_targets = os.getenv("INGEST_UNTIL_TARGETS", "true").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    poll_seconds = float(os.getenv("INGESTION_POLL_SECONDS", "30"))
    max_seconds = int(os.getenv("INGEST_CATCHUP_MAX_SECONDS", "600"))
    max_workers = int(os.getenv("INGESTION_MAX_WORKERS", "1"))
    batch_size = int(os.getenv("INGESTION_BATCH_SIZE", "10"))
    loop_sleep_seconds = float(os.getenv("INGESTION_LOOP_SLEEP_SECONDS", "10"))
    retry_base_seconds = int(os.getenv("INGESTION_RETRY_BASE_SECONDS", "5"))
    retry_max_seconds = int(os.getenv("INGESTION_RETRY_MAX_SECONDS", "300"))

    def _process_batch(row_id: int, batch: int) -> Dict[str, object]:
        if int(row_id) in low_yield_rss_row_ids:
            return {
                "row_id": row_id,
                "status": "skipped_low_ready_yield",
                "inserted": 0,
                "attempted": 0,
            }
        return _process_progress_row_batch(
            row_id=row_id,
            day_utc=day,
            redis_client=redis_client,
            owner_token=owner,
            ttl_ms=ttl_ms,
            batch_size=batch,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )

    _prime_bootstrap_youtube_rows(
        db=db,
        repo=repo,
        day_utc=day,
        process_batch=_process_batch,
        default_batch_size=batch_size,
    )

    if max_workers <= 1:
        loop_repo = repo
        if low_yield_rss_sources:
            class _YieldGuardedRepo:
                def __getattr__(self, name):
                    return getattr(repo, name)

                def list_incomplete(self, *, day_utc):
                    return [
                        row
                        for row in repo.list_incomplete(day_utc=day_utc)
                        if not (
                            row.source_type == "rss"
                            and row.feed_name in low_yield_rss_sources
                        )
                    ]

            loop_repo = _YieldGuardedRepo()

        return _run_checkpoint_loop(
            day=day,
            repo=loop_repo,
            process_row=lambda rid: _process_batch(int(rid), batch_size),
            ingest_until_targets=ingest_until_targets,
            poll_seconds=poll_seconds,
            max_seconds=max_seconds,
            max_workers=max_workers,
            stop_event=STOP_EVENT,
        )

    # Parallel fair scheduler
    from app.ingestion.runtime_state import set_scheduler_snapshot
    from app.ingestion.scheduler import IngestionScheduler, TaskRef

    scheduler = IngestionScheduler(day_utc=day, redis_client=redis_client)

    def _fetch_tasks() -> List[TaskRef]:
        # Separate DB session in scheduler thread.
        from app.db.base import SessionLocal
        from app.models.ingestion_budget import IngestionBudget

        sdb = SessionLocal()
        try:
            srepo = IngestionProgressRepository(sdb)
            rows = srepo.list_eligible(day_utc=day, limit=200)

            budget_rows = sdb.query(IngestionBudget).filter(IngestionBudget.day == day).all()
            remaining_by_type = {
                b.content_type.value: max(
                    0, int(b.target or 0) - int(b.inserted or 0) - int(b.reserved or 0)
                )
                for b in budget_rows
            }

            def _ct_for_source_type(st: str) -> str:
                if st == "rss":
                    return "ARTICLE"
                if st == "youtube_video":
                    return "VIDEO"
                if st == "youtube_reel":
                    return "REEL"
                return st

            tasks: List[TaskRef] = []
            for r in rows:
                if r.source_type == "rss" and r.feed_name in low_yield_rss_sources:
                    continue
                ct = _ct_for_source_type(r.source_type)
                if remaining_by_type.get(ct, 0) <= 0:
                    continue
                tasks.append(
                    TaskRef(row_id=int(r.id), source_type=r.source_type, feed_name=r.feed_name)
                )

            return tasks
        finally:
            sdb.close()

    def _on_stats(stats):
        try:
            set_scheduler_snapshot(stats.__dict__)
        except Exception:
            return

    return scheduler.run(
        fetch_tasks=_fetch_tasks,
        process_task_batch=lambda row_id, bsz: _process_batch(int(row_id), int(bsz)),
        stop_event=STOP_EVENT,
        max_seconds=max_seconds,
        sleep_seconds=loop_sleep_seconds,
        on_stats=_on_stats,
    )


__all__ = [
    "FeedDefault",
    "STOP_EVENT",
    "install_signal_handlers",
    "run_checkpointed_ingestion",
    # internal helpers used by unit tests
    "_run_checkpoint_loop",
    "_process_progress_row_batch",
]
