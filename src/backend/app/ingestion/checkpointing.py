"""Checkpointed ingestion runner.

Implements durable, restart-resilient ingestion using:
- Postgres table `ingestion_progress` for per-feed checkpoints
- Redis per-feed leases to ensure only one process ingests a feed at a time
- Postgres advisory lock fallback when Redis is unavailable

This intentionally focuses on safe/idempotent inserts (ON CONFLICT DO NOTHING).
Scoring/clustering can be handled by existing scheduled jobs.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import signal
import threading
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.ingestion.leases import claim_lease, lease_key, release_lease
from app.ingestion.time import get_ingestion_day
from app.ingestion.url_normalizer import normalize_url
from app.models.content import ContentItem, ContentType
from app.repositories.ingestion_progress_repo import IngestionProgressRepository

logger = get_logger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_client import YouTubeClient


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


@dataclass(frozen=True)
class FeedDefault:
    source_type: str
    feed_name: str
    target: int


def _owner_token() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def _parse_target_overrides() -> Dict[str, int]:
    """Parse INGESTION_TARGET_DEFAULTS.

    Format: JSON object mapping "{source_type}:{feed_name}" -> int.
    """
    raw = os.getenv("INGESTION_TARGET_DEFAULTS", "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("Invalid INGESTION_TARGET_DEFAULTS JSON; ignoring")
        return {}

    result: Dict[str, int] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                result[str(k)] = int(v)
            except Exception:
                continue
    return result


def _build_defaults() -> List[FeedDefault]:
    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_channels import ContentFormat
    from app.integrations.youtube_client import YouTubeClient

    overrides = _parse_target_overrides()

    defaults: List[FeedDefault] = []

    # RSS: 1 row per feed
    rss_client = RSSClient()
    for cfg in rss_client.feed_configs:
        key = f"rss:{cfg.name}"
        target = int(overrides.get(key, cfg.daily_cap))
        defaults.append(FeedDefault("rss", cfg.name, max(0, target)))

    # YouTube: split per channel into video vs reel targets based on format
    yt_client = YouTubeClient()
    for cfg in yt_client.channel_configs:
        if cfg.content_format == ContentFormat.LONG_FORM:
            key = f"youtube_video:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, target)))
        elif cfg.content_format == ContentFormat.SHORTS:
            key = f"youtube_reel:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, target)))
        else:
            # MIXED: split daily_cap across video and reels
            video_target = max(1, int(cfg.daily_cap) // 2)
            reel_target = max(0, int(cfg.daily_cap) - video_target)

            key_v = f"youtube_video:{cfg.name}"
            key_r = f"youtube_reel:{cfg.name}"
            video_target = int(overrides.get(key_v, video_target))
            reel_target = int(overrides.get(key_r, reel_target))

            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, video_target)))
            if reel_target > 0:
                defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, reel_target)))

    # Filter disabled/zero targets
    return [d for d in defaults if d.target > 0]


def _try_pg_advisory_lock(db: Session, key: str) -> bool:
    try:
        # Use hashtext(key) for a stable 32-bit key in Postgres.
        result = db.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}).scalar()
        return bool(result)
    except Exception:
        return False


def _pg_advisory_unlock(db: Session, key: str) -> None:
    try:
        db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
        db.commit()
    except Exception:
        db.rollback()


def _rss_feed_config(rss: RSSClient, feed_name: str):
    for cfg in rss.feed_configs:
        if cfg.name == feed_name:
            return cfg
    return None


def _yt_channel_config(yt: YouTubeClient, channel_name: str):
    for cfg in yt.channel_configs:
        if cfg.name == channel_name:
            return cfg
    return None


def _cursor_from_rss_entry(entry) -> str:
    return normalize_url(entry.url) if entry.url else ""


def _cursor_from_yt_entry(entry) -> str:
    return entry.video_id or ""


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _insert_content_items_postgres(
    db: Session,
    *,
    values: List[dict],
) -> int:
    if not values:
        return 0

    stmt = (
        pg_insert(ContentItem)
        .values(values)
        .on_conflict_do_nothing(index_elements=["source_url"])
        .returning(ContentItem.id)
    )
    rows = db.execute(stmt).fetchall()
    return len(rows)


def _process_progress_row_batch(
    *,
    row_id: int,
    day_utc: date,
    redis_client,
    owner_token: str,
    ttl_ms: int,
    batch_size: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> Dict[str, object]:
    """Process a single progress row for one batch in its own session."""
    from app.db.base import SessionLocal
    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_client import YouTubeClient

    db = SessionLocal()
    repo = IngestionProgressRepository(db)

    rss = RSSClient()
    yt = YouTubeClient()

    from app.models.ingestion_progress import IngestionProgress

    progress = db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()

    # Respect backoff.
    now = datetime.utcnow()
    if progress.retry_at is not None and progress.retry_at > now:
        return {"row_id": row_id, "status": "skipped_backoff", "inserted": 0, "attempted": 0}

    if progress.status == "complete" or int(progress.items_ingested) >= int(progress.target):
        return {"row_id": row_id, "status": "complete", "inserted": 0, "attempted": 0}

    scope_key = f"{day_utc.isoformat()}:{progress.source_type}:{progress.feed_name}"
    redis_key = lease_key(day_utc.isoformat(), progress.source_type, progress.feed_name)

    acquired = False
    used_pg_lock = False

    if redis_client is not None:
        acquired = claim_lease(redis_client, key=redis_key, owner_token=owner_token, ttl_ms=ttl_ms)
        if acquired:
            logger.info("Lease claimed: %s", scope_key)
    if not acquired:
        # Fallback to Postgres advisory lock
        acquired = _try_pg_advisory_lock(db, f"ingestion:{scope_key}")
        used_pg_lock = acquired
        if acquired:
            logger.info("Advisory lock claimed: %s", scope_key)

    if not acquired:
        return {"row_id": row_id, "status": "skipped_locked", "inserted": 0, "attempted": 0}

    try:
        inserted = 0
        attempted = 0

        if progress.source_type == "rss":
            cfg = _rss_feed_config(rss, progress.feed_name)
            if not cfg:
                repo.mark_failed(row_id, f"Unknown RSS feed: {progress.feed_name}")
                return {"row_id": row_id, "status": "failed", "inserted": 0}

            max_entries = int(os.getenv("RSS_ENTRIES_PER_FEED", "50"))
            entries = rss.fetch_feed(cfg.url, max_entries=max_entries)
            if not entries:
                return {"row_id": row_id, "status": "no_entries", "inserted": 0, "attempted": 0}

            # Build candidates from newest + backfill, with a multiplier to overcome duplicates.
            remaining = max(0, int(progress.target) - int(progress.items_ingested))
            new_window = min(batch_size, max(1, remaining))
            multiplier = max(1, _int_env("INGESTION_CANDIDATE_MULTIPLIER", 5))
            candidate_limit = max(new_window, batch_size * multiplier)

            values: List[dict] = []
            last_scanned_cursor: Optional[str] = progress.last_item_cursor

            def _add_entry(e) -> None:
                nonlocal last_scanned_cursor
                ec = _cursor_from_rss_entry(e)
                if ec:
                    last_scanned_cursor = ec

                source_url = normalize_url(e.url) if e.url else e.url
                if not source_url:
                    return

                source = extract_source(source_url)
                topics = extract_topics(e.title, (e.content or "")[:500])
                entities = extract_entities(e.title, "")

                values.append(
                    {
                        "type": ContentType.ARTICLE,
                        "source": source,
                        "source_url": source_url,
                        "canonical_url": None,
                        "published_at": e.published_date or datetime.utcnow(),
                        "title": e.title,
                        "description": (e.content or "")[:500] if e.content else None,
                        "image_url": e.image_url,
                        "video_url": None,
                        "summary": None,
                        "ai_processed": False,
                        "topics": topics or [],
                        "entities": entities or [],
                        "quality_score": 0.5,
                        "trend_score": 0.0,
                        "recency_score": 1.0,
                        "diversity_boost": 0.0,
                        "global_score": 0.0,
                        "cluster_id": None,
                        "is_cluster_canonical": 0,
                        "dedupe_key": None,
                        "duration_seconds": None,
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                )

            # Pass 1: always consider newest items.
            for entry in entries:
                if len(values) >= new_window:
                    break
                _add_entry(entry)

            # Pass 2: backfill beyond the saved cursor to reach candidate_limit.
            if len(values) < candidate_limit:
                start_idx = -1
                if progress.last_item_cursor:
                    for i, e in enumerate(entries):
                        if _cursor_from_rss_entry(e) == progress.last_item_cursor:
                            start_idx = i
                            break
                # If cursor not found, start after the newest window.
                if start_idx < 0:
                    start_idx = new_window - 1
                for e in entries[start_idx + 1 :]:
                    if len(values) >= candidate_limit:
                        break
                    _add_entry(e)

            new_cursor = last_scanned_cursor

            # Commit inserts + progress update atomically.
            try:
                inserted = _insert_content_items_postgres(db, values=values)
                attempted = len(values)

                # Progress update in same transaction as inserts
                from app.models.ingestion_progress import IngestionProgress

                db.query(IngestionProgress).filter(IngestionProgress.id == row_id).update(
                    {
                        IngestionProgress.items_ingested: IngestionProgress.items_ingested + inserted,
                        IngestionProgress.items_attempted: IngestionProgress.items_attempted + attempted,
                        IngestionProgress.last_item_cursor: new_cursor,
                        IngestionProgress.status: "complete"
                        if (int(progress.items_ingested) + inserted) >= int(progress.target)
                        else "running",
                        IngestionProgress.retry_at: None,
                        IngestionProgress.updated_at: datetime.utcnow(),
                    }
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        elif progress.source_type in ("youtube_video", "youtube_reel"):
            cfg = _yt_channel_config(yt, progress.feed_name)
            if not cfg:
                repo.mark_failed(row_id, f"Unknown YouTube channel: {progress.feed_name}")
                return {"row_id": row_id, "status": "failed", "inserted": 0}

            max_videos = int(os.getenv("YT_VIDEOS_PER_CHANNEL", "30"))
            entries = yt._fetch_channel_with_config(cfg, max_videos=max_videos)  # noqa: SLF001
            if not entries:
                return {"row_id": row_id, "status": "no_entries", "inserted": 0, "attempted": 0}
            want_reel = progress.source_type == "youtube_reel"

            remaining = max(0, int(progress.target) - int(progress.items_ingested))
            new_window = min(batch_size, max(1, remaining))
            multiplier = max(1, _int_env("INGESTION_CANDIDATE_MULTIPLIER", 5))
            candidate_limit = max(new_window, batch_size * multiplier)

            values = []
            last_scanned_cursor: Optional[str] = progress.last_item_cursor

            def _add_entry(e) -> None:
                nonlocal last_scanned_cursor
                entry_cursor = _cursor_from_yt_entry(e)
                if entry_cursor:
                    last_scanned_cursor = entry_cursor

                is_reel = bool(e.is_short or (e.video_url and "/shorts/" in e.video_url))
                if want_reel != is_reel:
                    return

                source_url = normalize_url(e.video_url) if e.video_url else e.video_url
                if not source_url:
                    return

                values.append(
                    {
                        "type": ContentType.REEL if is_reel else ContentType.VIDEO,
                        "source": e.source or "YouTube",
                        "source_url": source_url,
                        "canonical_url": None,
                        "published_at": datetime.utcnow(),
                        "title": e.title,
                        "description": (e.summary or "")[:500] if e.summary else None,
                        "image_url": e.thumbnail_url,
                        "video_url": source_url,
                        "summary": None,
                        "ai_processed": False,
                        "topics": extract_topics(e.title, (e.summary or "")[:500]) or [],
                        "entities": extract_entities(e.title, "") or [],
                        "quality_score": 0.5,
                        "trend_score": 0.0,
                        "recency_score": 1.0,
                        "diversity_boost": 0.0,
                        "global_score": 0.0,
                        "cluster_id": None,
                        "is_cluster_canonical": 0,
                        "dedupe_key": f"yt:{e.video_id}" if e.video_id else None,
                        "duration_seconds": None,
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                )

            # Pass 1: always consider newest items.
            for entry in entries:
                if len(values) >= new_window:
                    break
                _add_entry(entry)

            # Pass 2: backfill beyond cursor to reach candidate_limit.
            if len(values) < candidate_limit:
                start_idx = -1
                if progress.last_item_cursor:
                    for i, e in enumerate(entries):
                        if _cursor_from_yt_entry(e) == progress.last_item_cursor:
                            start_idx = i
                            break
                if start_idx < 0:
                    start_idx = new_window - 1
                for e in entries[start_idx + 1 :]:
                    if len(values) >= candidate_limit:
                        break
                    _add_entry(e)

            new_cursor = last_scanned_cursor

            # Commit inserts + progress update atomically.
            try:
                inserted = _insert_content_items_postgres(db, values=values)
                attempted = len(values)
                from app.models.ingestion_progress import IngestionProgress

                db.query(IngestionProgress).filter(IngestionProgress.id == row_id).update(
                    {
                        IngestionProgress.items_ingested: IngestionProgress.items_ingested + inserted,
                        IngestionProgress.items_attempted: IngestionProgress.items_attempted + attempted,
                        IngestionProgress.last_item_cursor: new_cursor,
                        IngestionProgress.status: "complete"
                        if (int(progress.items_ingested) + inserted) >= int(progress.target)
                        else "running",
                        IngestionProgress.retry_at: None,
                        IngestionProgress.updated_at: datetime.utcnow(),
                    }
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        else:
            repo.mark_failed(row_id, f"Unknown source_type: {progress.source_type}")
            return {"row_id": row_id, "status": "failed", "inserted": 0}

        if inserted or attempted:
            logger.info("Progress persisted: %s attempted=%s inserted=%s", scope_key, attempted, inserted)
        return {"row_id": row_id, "status": "ok", "inserted": inserted, "attempted": attempted}

    except Exception as e:
        logger.exception("Ingestion failed for %s", scope_key)
        try:
            # Retry/backoff scheduling (cap at retry_max_seconds)
            retry_count = int(progress.retry_count or 0)
            delay = min(retry_max_seconds, max(retry_base_seconds, retry_base_seconds * (2 ** retry_count)))
            retry_at = datetime.utcnow() + timedelta(seconds=int(delay))
            repo.schedule_retry(row_id=row_id, error=str(e), retry_at=retry_at)
        except Exception:
            pass
        return {"row_id": row_id, "status": "failed", "inserted": 0, "attempted": 0, "error": str(e)}

    finally:
        if redis_client is not None:
            released = release_lease(redis_client, key=redis_key, owner_token=owner_token)
            if released:
                logger.info("Lease released: %s", scope_key)
        if used_pg_lock:
            _pg_advisory_unlock(db, f"ingestion:{scope_key}")

        db.close()


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

    defaults = _build_defaults()
    created = repo.ensure_rows(day_utc=day, defaults=[(d.source_type, d.feed_name, d.target) for d in defaults])
    if created:
        logger.info("Created %s ingestion_progress rows for %s", created, day.isoformat())

    owner = _owner_token()
    ttl_ms = int(os.getenv("INGESTION_LEASE_TTL_MS", "60000"))

    ingest_until_targets = os.getenv("INGEST_UNTIL_TARGETS", "true").lower() in ("true", "1", "yes", "on")
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

        sdb = SessionLocal()
        try:
            srepo = IngestionProgressRepository(sdb)
            rows = srepo.list_eligible(day_utc=day, limit=200)
            return [TaskRef(row_id=int(r.id), source_type=r.source_type, feed_name=r.feed_name) for r in rows]
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


def _run_checkpoint_loop(
    *,
    day: date,
    repo: IngestionProgressRepository,
    process_row: Callable[[int], Dict[str, object]],
    ingest_until_targets: bool,
    poll_seconds: float,
    max_seconds: int,
    max_workers: int,
    stop_event: threading.Event,
) -> Dict[str, object]:
    """Core resume loop (unit-testable)."""
    start = time.monotonic()
    total_inserted = 0

    while True:
        if stop_event.is_set():
            return {
                "status": "stopping",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
            }

        incomplete = repo.list_incomplete(day_utc=day)
        if not incomplete:
            return {
                "status": "complete",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
            }

        if max_workers <= 1:
            for row in incomplete:
                if stop_event.is_set():
                    return {
                        "status": "stopping",
                        "day_utc": day.isoformat(),
                        "total_inserted": total_inserted,
                    }

                if time.monotonic() - start >= max_seconds:
                    return {
                        "status": "budget_exhausted",
                        "day_utc": day.isoformat(),
                        "total_inserted": total_inserted,
                        "remaining_rows": len(incomplete),
                    }

                result = process_row(int(row.id))
                total_inserted += int(result.get("inserted") or 0)
        else:
            from concurrent.futures import ThreadPoolExecutor

            ids = [int(r.id) for r in incomplete]
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for result in ex.map(process_row, ids):
                    total_inserted += int((result or {}).get("inserted") or 0)

        if not ingest_until_targets:
            return {
                "status": "partial",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
                "remaining_rows": len(repo.list_incomplete(day_utc=day)),
            }

        if time.monotonic() - start >= max_seconds:
            return {
                "status": "budget_exhausted",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
                "remaining_rows": len(repo.list_incomplete(day_utc=day)),
            }

        time.sleep(poll_seconds)
