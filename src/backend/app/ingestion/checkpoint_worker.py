"""Worker logic for a single ingestion_progress row batch."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.curation import review_queue_target_status
from app.core.logging import get_logger
from app.ingestion.canonical import canonical_key_for_article, canonical_key_for_youtube
from app.ingestion.checkpoint_locks import pg_advisory_unlock, try_pg_advisory_lock
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.ingestion.language_filter import is_english
from app.ingestion.leases import claim_lease, lease_key, release_lease
from app.ingestion.url_normalizer import normalize_url
from app.models.content import ContentItem, ContentType
from app.repositories.ingestion_budget_repo import IngestionBudgetRepository
from app.repositories.ingestion_progress_repo import IngestionProgressRepository

logger = get_logger(__name__)


def _rss_feed_config(rss, feed_name: str):
    for cfg in rss.feed_configs:
        if cfg.name == feed_name:
            return cfg
    return None


def _yt_channel_config(yt, channel_name: str):
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


def _insert_content_items_postgres(db: Session, *, values: List[dict]) -> int:
    if not values:
        return 0

    stmt = (
        pg_insert(ContentItem)
        .values(values)
        # Do not target a single constraint; we may dedupe via either source_url or canonical_key.
        .on_conflict_do_nothing()
        .returning(ContentItem.id)
    )
    rows = db.execute(stmt).fetchall()
    return len(rows)


def process_progress_row_batch(
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
    budget_repo = IngestionBudgetRepository(db)

    rss = RSSClient()
    yt = YouTubeClient()

    from app.models.ingestion_progress import IngestionProgress

    progress = db.query(IngestionProgress).filter(IngestionProgress.id == row_id).one()
    review_queue_status = review_queue_target_status()
    logger.info(
        f"Processing row: id={row_id} type={progress.source_type} feed={progress.feed_name} target={progress.target} ingested={progress.items_ingested} status={progress.status}"
    )

    # Respect backoff.
    now = datetime.utcnow()
    if progress.retry_at is not None and progress.retry_at > now:
        return {"row_id": row_id, "status": "skipped_backoff", "inserted": 0, "attempted": 0}

    if progress.status == "complete" or int(progress.items_ingested) >= int(progress.target):
        return {"row_id": row_id, "status": "complete", "inserted": 0, "attempted": 0}

    max_attempts_multiplier = _int_env("INGESTION_MAX_ATTEMPTS_MULTIPLIER", 30)
    max_attempts_min = _int_env("INGESTION_MAX_ATTEMPTS_MIN", 50)
    max_attempts = max(
        int(max_attempts_min), int(progress.target or 0) * int(max_attempts_multiplier)
    )
    if int(progress.items_attempted or 0) >= max_attempts and int(
        progress.items_ingested or 0
    ) < int(progress.target or 0):
        repo.mark_failed(
            row_id,
            f"Exhausted attempts: attempted={int(progress.items_attempted or 0)} max={max_attempts}",
        )
        return {"row_id": row_id, "status": "exhausted", "inserted": 0, "attempted": 0}

    scope_key = f"{day_utc.isoformat()}:{progress.source_type}:{progress.feed_name}"
    redis_key = lease_key(day_utc.isoformat(), progress.source_type, progress.feed_name)

    acquired = False
    used_pg_lock = False

    if redis_client is not None:
        acquired = claim_lease(redis_client, key=redis_key, owner_token=owner_token, ttl_ms=ttl_ms)
        if acquired:
            logger.info("Lease claimed: %s", scope_key)
    if not acquired:
        acquired = try_pg_advisory_lock(db, f"ingestion:{scope_key}")
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
            logger.info(
                f"RSS fetch: feed={progress.feed_name} url={cfg.url} max_entries={max_entries}"
            )
            entries = rss.fetch_feed(cfg.url, max_entries=max_entries)
            if not entries:
                logger.info(f"RSS fetch: feed={progress.feed_name} no entries returned")
                return {"row_id": row_id, "status": "no_entries", "inserted": 0, "attempted": 0}

            remaining = max(0, int(progress.target) - int(progress.items_ingested))
            logger.info(
                f"RSS ingestion: feed={progress.feed_name} entries={len(entries)} target={progress.target} ingested={progress.items_ingested} remaining={remaining}"
            )

            reserved = budget_repo.reserve(
                day=day_utc, content_type=ContentType.ARTICLE, want=min(batch_size, remaining)
            )
            if reserved <= 0:
                logger.info(
                    f"RSS budget full: feed={progress.feed_name} - ARTICLE budget exhausted"
                )
                return {
                    "row_id": row_id,
                    "status": "skipped_type_full",
                    "inserted": 0,
                    "attempted": 0,
                }

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

                # Language gate: skip non-English content
                if not is_english(e.title, e.content):
                    return

                source_url = normalize_url(e.url) if e.url else e.url
                if not source_url:
                    return

                source = extract_source(source_url)
                topics = extract_topics(e.title, (e.content or "")[:500])
                entities = extract_entities(e.title, "")

                values.append(
                    {
                        "type": ContentType.ARTICLE,
                        "curation_status": review_queue_status,
                        "discovered_via": "rss_ingestion",
                        "source": source,
                        "source_url": source_url,
                        "canonical_url": source_url,
                        "canonical_key": canonical_key_for_article(
                            canonical_url=source_url, source_url=source_url
                        ),
                        "ingestion_day": day_utc,
                        "is_suppressed": False,
                        "signal_hits": 0,
                        "published_at": e.published_date or datetime.utcnow(),
                        "title": e.title,
                        "description": (e.content or "")[:500] if e.content else None,
                        "content_text": (e.content or "")[:8000] if e.content else None,
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
                        "simhash": None,
                        "dedupe_key": None,
                        "duration_seconds": None,
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                )

            for entry in entries:
                if len(values) >= new_window:
                    break
                _add_entry(entry)

            if len(values) < candidate_limit:
                start_idx = -1
                if progress.last_item_cursor:
                    for i, e in enumerate(entries):
                        if _cursor_from_rss_entry(e) == progress.last_item_cursor:
                            start_idx = i
                            break
                if start_idx < 0:
                    start_idx = new_window - 1
                for e in entries[start_idx + 1 :]:
                    if len(values) >= candidate_limit:
                        break
                    _add_entry(e)

            new_cursor = last_scanned_cursor

            try:
                inserted = 0
                attempted = 0
                remaining_slots = int(reserved)
                idx = 0
                while remaining_slots > 0 and idx < len(values):
                    chunk = values[idx : idx + remaining_slots]
                    if not chunk:
                        break
                    attempted += len(chunk)
                    ins = _insert_content_items_postgres(db, values=chunk)
                    inserted += int(ins)
                    remaining_slots -= int(ins)
                    idx += len(chunk)

                budget_repo.finalize_batch(
                    day=day_utc,
                    content_type=ContentType.ARTICLE,
                    reserved_taken=reserved,
                    inserted=inserted,
                    seen=len(values),
                    suppressed=max(0, attempted - inserted),
                    attempts=attempted,
                )

                db.query(IngestionProgress).filter(IngestionProgress.id == row_id).update(
                    {
                        IngestionProgress.items_ingested: IngestionProgress.items_ingested
                        + inserted,
                        IngestionProgress.items_attempted: IngestionProgress.items_attempted
                        + attempted,
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

            # Debug: log channel config info
            logger.info(
                "YT channel config: name=%s content_format=%s",
                cfg.name,
                cfg.content_format.value if cfg.content_format else "None",
            )

            max_videos = int(os.getenv("YT_VIDEOS_PER_CHANNEL", "30"))
            entries = yt._fetch_channel_with_config(cfg, max_videos=max_videos)  # noqa: SLF001
            if not entries:
                logger.info("YT fetch: no entries from %s", progress.feed_name)
                return {"row_id": row_id, "status": "no_entries", "inserted": 0, "attempted": 0}

            # Debug: log is_short for each entry
            shorts_count = sum(1 for e in entries if e.is_short)
            logger.info(
                "YT fetch entries: channel=%s total=%d shorts=%d longs=%d",
                progress.feed_name,
                len(entries),
                shorts_count,
                len(entries) - shorts_count,
            )

            want_reel = progress.source_type == "youtube_reel"
            lookback_cutoff = datetime.utcnow() - timedelta(
                hours=settings.YT_CURATED_LOOKBACK_HOURS
            )

            remaining = max(0, int(progress.target) - int(progress.items_ingested))

            budget_type = ContentType.REEL if want_reel else ContentType.VIDEO
            reserved = budget_repo.reserve(
                day=day_utc, content_type=budget_type, want=min(batch_size, remaining)
            )
            if reserved <= 0:
                logger.info("YT budget full for %s (type=%s)", progress.feed_name, budget_type)
                return {
                    "row_id": row_id,
                    "status": "skipped_type_full",
                    "inserted": 0,
                    "attempted": 0,
                }

            new_window = min(batch_size, max(1, remaining))
            multiplier = max(1, _int_env("INGESTION_CANDIDATE_MULTIPLIER", 5))
            candidate_limit = max(new_window, batch_size * multiplier)

            logger.info(
                "YT processing: feed=%s want_reel=%s reserved=%d new_window=%d candidate_limit=%d entries=%d",
                progress.feed_name,
                want_reel,
                reserved,
                new_window,
                candidate_limit,
                len(entries),
            )

            values: List[dict] = []
            last_scanned_cursor: Optional[str] = progress.last_item_cursor
            skipped_reasons: dict = {"is_short_mismatch": 0, "no_source_url": 0}

            def _add_entry(e) -> None:
                nonlocal last_scanned_cursor
                entry_cursor = _cursor_from_yt_entry(e)
                if entry_cursor:
                    last_scanned_cursor = entry_cursor

                is_reel = bool(e.is_short or (e.video_url and "/shorts/" in e.video_url))
                # Debug: trace the filter decision
                logger.debug(
                    "YT filter: want_reel=%s is_reel=%s e.is_short=%s url_has_shorts=%s video_id=%s",
                    want_reel,
                    is_reel,
                    e.is_short,
                    "/shorts/" in (e.video_url or ""),
                    e.video_id,
                )
                if want_reel != is_reel:
                    skipped_reasons["is_short_mismatch"] += 1
                    return

                published_at = getattr(e, "published_at", None)
                if published_at and published_at < lookback_cutoff:
                    skipped_reasons.setdefault("outside_lookback", 0)
                    skipped_reasons["outside_lookback"] += 1
                    return

                # Language gate: skip non-English content
                if not is_english(e.title, getattr(e, "summary", None)):
                    skipped_reasons.setdefault("non_english", 0)
                    skipped_reasons["non_english"] += 1
                    return

                source_url = normalize_url(e.video_url) if e.video_url else e.video_url
                if not source_url:
                    skipped_reasons["no_source_url"] += 1
                    return

                values.append(
                    {
                        "type": ContentType.REEL if is_reel else ContentType.VIDEO,
                        "curation_status": review_queue_status,
                        "discovered_via": f"yt_{getattr(e, 'acquisition_lane', 'curated')}",
                        "source": e.source or "YouTube",
                        "source_url": source_url,
                        "canonical_url": source_url,
                        "channel_id": getattr(e, "channel_id", None),
                        "canonical_key": canonical_key_for_youtube(
                            video_id=getattr(e, "video_id", None),
                            source_url=source_url,
                            video_url=source_url,
                        ),
                        "ingestion_day": day_utc,
                        "is_suppressed": False,
                        "signal_hits": 0,
                        "published_at": published_at or datetime.utcnow(),
                        "title": e.title,
                        "description": (e.summary or "")[:500] if e.summary else None,
                        "content_text": None,
                        "image_url": e.thumbnail_url,
                        "video_url": source_url,
                        "summary": None,
                        "ai_processed": False,
                        "topics": extract_topics(e.title, (e.summary or "")[:500]) or [],
                        "entities": extract_entities(e.title, (e.summary or "")[:500]) or [],
                        "quality_score": 0.5,
                        "trend_score": 0.0,
                        "recency_score": 1.0,
                        "diversity_boost": 0.0,
                        "global_score": 0.0,
                        "acquisition_lane": getattr(e, "acquisition_lane", "curated"),
                        "source_status": getattr(e, "source_status", None),
                        "view_count_snapshot": getattr(e, "view_count", None),
                        "engagement_snapshot": {
                            "likes": getattr(e, "like_count", None),
                            "comments": getattr(e, "comment_count", None),
                        },
                        "views_per_hour": getattr(e, "views_per_hour", None),
                        "format_fit_score": getattr(e, "format_fit_score", None),
                        "cluster_id": None,
                        "is_cluster_canonical": 0,
                        "simhash": None,
                        "dedupe_key": f"yt:{e.video_id}" if e.video_id else None,
                        "duration_seconds": getattr(e, "duration_seconds", None),
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                )

            for entry in entries:
                if len(values) >= new_window:
                    break
                _add_entry(entry)

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

            # Debug logging for video/reel ingestion
            logger.info(
                "YT ingestion: type=%s feed=%s entries=%d values=%d want_reel=%s reserved=%d skipped=%s",
                progress.source_type,
                progress.feed_name,
                len(entries),
                len(values),
                want_reel,
                reserved,
                skipped_reasons,
            )

            try:
                inserted = 0
                attempted = 0
                remaining_slots = int(reserved)
                idx = 0
                while remaining_slots > 0 and idx < len(values):
                    chunk = values[idx : idx + remaining_slots]
                    if not chunk:
                        break
                    attempted += len(chunk)
                    ins = _insert_content_items_postgres(db, values=chunk)
                    inserted += int(ins)
                    remaining_slots -= int(ins)
                    idx += len(chunk)

                budget_repo.finalize_batch(
                    day=day_utc,
                    content_type=budget_type,
                    reserved_taken=reserved,
                    inserted=inserted,
                    seen=len(values),
                    suppressed=max(0, attempted - inserted),
                    attempts=attempted,
                )

                db.query(IngestionProgress).filter(IngestionProgress.id == row_id).update(
                    {
                        IngestionProgress.items_ingested: IngestionProgress.items_ingested
                        + inserted,
                        IngestionProgress.items_attempted: IngestionProgress.items_attempted
                        + attempted,
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
            logger.info(
                "Progress persisted: %s attempted=%s inserted=%s", scope_key, attempted, inserted
            )
        return {"row_id": row_id, "status": "ok", "inserted": inserted, "attempted": attempted}

    except Exception as e:
        logger.exception("Ingestion failed for %s", scope_key)
        try:
            retry_count = int(progress.retry_count or 0)
            delay = min(
                retry_max_seconds, max(retry_base_seconds, retry_base_seconds * (2**retry_count))
            )
            retry_at = datetime.utcnow() + timedelta(seconds=int(delay))
            repo.schedule_retry(row_id=row_id, error=str(e), retry_at=retry_at)
        except Exception:
            pass
        return {
            "row_id": row_id,
            "status": "failed",
            "inserted": 0,
            "attempted": 0,
            "error": str(e),
        }

    finally:
        if redis_client is not None:
            released = release_lease(redis_client, key=redis_key, owner_token=owner_token)
            if released:
                logger.info("Lease released: %s", scope_key)
        if used_pg_lock:
            pg_advisory_unlock(db, f"ingestion:{scope_key}")

        db.close()
