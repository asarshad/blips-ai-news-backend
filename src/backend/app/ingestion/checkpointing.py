"""Checkpointed ingestion runner.

This module is a thin public facade to keep imports stable.

Implementation details live in smaller modules:
- app.ingestion.checkpoint_defaults
- app.ingestion.checkpoint_worker
- app.ingestion.checkpoint_loop
"""

import os
import signal
import threading
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.checkpoint_defaults import FeedDefault
from app.ingestion.checkpoint_defaults import build_defaults as _build_defaults
from app.ingestion.checkpoint_defaults import owner_token as _owner_token
from app.ingestion.checkpoint_loop import run_checkpoint_loop as _run_checkpoint_loop
from app.ingestion.checkpoint_worker import (
    process_progress_row_batch as _process_progress_row_batch,
)
from app.ingestion.time import get_ingestion_day
from app.models.content import ContentType
from app.repositories.ingestion_budget_repo import IngestionBudgetRepository
from app.repositories.ingestion_progress_repo import IngestionProgressRepository
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.inventory_service import invalidate_health_cache
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

logger = get_logger(__name__)

STOP_EVENT = threading.Event()


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

    # Reopen continuous-ingestion rows so sources can be revisited within the same UTC day.
    reopened = repo.reopen_continuous_rows(day_utc=day, defaults=defaults_tuples)
    if reopened:
        logger.info("Reopened %s YouTube progress rows for %s", reopened, day.isoformat())

    # Ensure strict per-type budgets from configured per-feed targets.
    # Videos/reels use a high multiplier so the budget never blocks continuous
    # ingestion (budget table kept for Phase A concurrency safety; daily cap
    # enforcement is removed).
    article_target = sum(d.target for d in defaults if d.source_type == "rss")
    video_target = sum(d.target for d in defaults if d.source_type == "youtube_video")
    reel_target = sum(d.target for d in defaults if d.source_type == "youtube_reel")
    budget_repo.ensure(day=day, content_type=ContentType.ARTICLE, target=article_target)
    budget_repo.ensure(day=day, content_type=ContentType.VIDEO, target=video_target * 10)
    budget_repo.ensure(day=day, content_type=ContentType.REEL, target=reel_target * 10)

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
        return _run_checkpoint_loop(
            day=day,
            repo=repo,
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
