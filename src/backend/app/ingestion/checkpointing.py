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

logger = get_logger(__name__)

STOP_EVENT = threading.Event()


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

    defaults = _build_defaults()
    created = repo.ensure_rows(
        day_utc=day, defaults=[(d.source_type, d.feed_name, d.target) for d in defaults]
    )
    if created:
        logger.info("Created %s ingestion_progress rows for %s", created, day.isoformat())

    # Ensure strict per-type budgets from configured per-feed targets.
    article_target = sum(d.target for d in defaults if d.source_type == "rss")
    video_target = sum(d.target for d in defaults if d.source_type == "youtube_video")
    reel_target = sum(d.target for d in defaults if d.source_type == "youtube_reel")
    budget_repo.ensure(day=day, content_type=ContentType.ARTICLE, target=article_target)
    budget_repo.ensure(day=day, content_type=ContentType.VIDEO, target=video_target)
    budget_repo.ensure(day=day, content_type=ContentType.REEL, target=reel_target)

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
