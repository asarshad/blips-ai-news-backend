"""Bounded major tech-news RSS probe.

This task intentionally reuses normal article insertion, promotion, AI, and
image-readiness paths. It only gives trusted major-news feeds a small, frequent
chance to enqueue fresh candidates.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update

from app.article_hydration import ArticleHydrationService
from app.core.curation import review_queue_target_status
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.ingestion.checkpoint_worker import (
    _build_rss_article_value,
    _insert_content_items_postgres,
    _queue_followup_events_for_inserted_ids,
)
from app.ingestion.url_normalizer import normalize_url
from app.integrations import LLMClient
from app.integrations.rss_client import RSSClient
from app.integrations.rss_feeds import FeedRole, get_feeds_by_role
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.repositories.source_fetch_state_repo import SourceFetchStateRepository
from app.scheduler.runtime import (
    current_worker_memory_mb,
    memory_over_soft_limit,
    memory_soft_limit_mb,
)
from app.services.article_image_service import ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE
from app.services.content_ai_service import CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE
from app.services.content_promotion_service import CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE
from app.services.major_news_constants import (
    MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE,
    MAJOR_NEWS_DISCOVERED_VIA,
    MAJOR_NEWS_RETRO_CLASSIFY_SOURCES,
    MAJOR_NEWS_SOURCE_TYPE,
)
from app.services.worker_lane_metrics import read_lane_heartbeats

logger = get_logger(__name__)

_MAJOR_NEWS_FAST_TRACK_EVENT_TYPES = (
    CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
    CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
    ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


def _maybe_classify_major_news_value(value: dict[str, Any], *, llm_client: Any | None) -> bool:
    """Persist bounded tech/major-news classifier metadata onto an insert payload."""
    title = str(value.get("title") or "").strip()
    summary = str(value.get("description") or value.get("content_text") or title).strip()
    source = str(value.get("source") or "").strip()
    url = str(value.get("source_url") or "").strip() or None

    if not llm_client or not getattr(llm_client, "is_configured", lambda: False)():
        value["is_major_tech_news"] = False
        value["major_tech_news_reason"] = "classifier_unavailable"
        return False
    if not summary:
        value["is_major_tech_news"] = False
        value["major_tech_news_reason"] = "missing_summary"
        return False

    try:
        tech = llm_client.classify_blips_tech_relevance(
            title=title,
            summary=summary,
            source=source,
            url=url,
        )
        value["tech_relevance"] = tech.is_blips_tech_relevant
        value["tech_relevance_confidence"] = tech.confidence
        value["tech_relevance_reason"] = tech.reason

        if (
            tech.is_blips_tech_relevant != "yes"
            or float(tech.confidence or 0.0) < MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE
        ):
            value["is_major_tech_news"] = False
            value["major_tech_news_confidence"] = float(tech.confidence or 0.0)
            value["major_tech_news_reason"] = (
                "Not eligible: Blips tech relevance was not confirmed."
            )
            return False

        major = llm_client.classify_major_tech_news(
            title=title,
            summary=summary,
            source=source,
            url=url,
        )
        is_major = major.is_major_tech_news == "yes"
        value["is_major_tech_news"] = is_major
        value["major_tech_news_confidence"] = major.confidence
        value["major_tech_news_reason"] = major.reason
        return is_major
    except Exception as exc:  # noqa: BLE001
        value["is_major_tech_news"] = False
        value["major_tech_news_reason"] = f"classifier_failed: {str(exc)[:220]}"
        logger.warning(
            "[major_news_probe] classifier failed source=%s url=%s: %s",
            source or "unknown",
            url or "",
            exc,
        )
        return False


def _fast_track_major_news_events(db, *, content_item_ids: list[int], now: datetime) -> int:
    if not content_item_ids:
        return 0
    result = db.execute(
        update(ContentEventOutbox)
        .where(
            ContentEventOutbox.content_item_id.in_(content_item_ids),
            ContentEventOutbox.event_type.in_(_MAJOR_NEWS_FAST_TRACK_EVENT_TYPES),
            ContentEventOutbox.status == "pending",
            ContentEventOutbox.available_at > now,
        )
        .values(available_at=now, updated_at=now)
    )
    return int(result.rowcount or 0)


def _ingestion_lane_recently_running(*, max_age_seconds: int = 180) -> bool:
    snapshot = read_lane_heartbeats()
    if not snapshot.get("available"):
        return False
    now = datetime.now(timezone.utc)
    for row in snapshot.get("lanes", []):
        if not isinstance(row, dict) or row.get("lane") != "ingestion":
            continue
        if str(row.get("status") or "").strip().lower() != "running":
            continue
        updated_raw = row.get("updated_at")
        if not isinstance(updated_raw, str) or not updated_raw:
            continue
        try:
            updated_at = datetime.fromisoformat(updated_raw)
        except ValueError:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return (now - updated_at.astimezone(timezone.utc)).total_seconds() <= max_age_seconds
    return False


def _entry_to_value(
    entry: Any,
    *,
    feed_name: str,
    day_utc: date,
    article_hydrator: ArticleHydrationService,
) -> dict | None:
    source_url = normalize_url(entry.url) if getattr(entry, "url", None) else ""
    if not source_url:
        return None
    value = _build_rss_article_value(
        entry,
        source_url=source_url,
        day_utc=day_utc,
        review_queue_status=review_queue_target_status(content_type=ContentType.ARTICLE),
        article_hydrator=article_hydrator,
    )
    if value is None:
        return None
    value["source"] = feed_name
    value["discovered_via"] = MAJOR_NEWS_DISCOVERED_VIA
    value["quality_score"] = max(float(value.get("quality_score") or 0.5), 0.82)
    return value


def _datetime_utc_naive(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _is_stale_for_major_news_probe(
    value: dict[str, Any],
    *,
    now: datetime,
    max_age_hours: int,
) -> bool:
    published_at = _datetime_utc_naive(value.get("published_at"))
    if published_at is None:
        return False
    return published_at < now - timedelta(hours=max_age_hours)


def _record_major_news_probe_skip(*, status: str, reason: str, now: datetime | None = None) -> None:
    """Record a lightweight major-news probe heartbeat even when fetch work is skipped."""
    current = now or datetime.utcnow()
    db = SessionLocal()
    try:
        state_repo = SourceFetchStateRepository(db)
        for feed in get_feeds_by_role(FeedRole.MAJOR_NEWS):
            state_repo.record_skip(
                source_type=MAJOR_NEWS_SOURCE_TYPE,
                feed_name=feed.name,
                source_url=feed.url,
                action=status,
                message=reason,
                now=current,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[major_news_probe] failed to record skip telemetry: %s", exc)
    finally:
        db.close()


def _retro_classify_premium_breaking(
    db,
    llm_client: Any | None,
    *,
    sources: list[str],
    max_items: int,
    lookback_hours: int,
    now: datetime,
) -> dict[str, int]:
    """Classify recent BREAKING-path items from premium sources that lack the major-news flag.

    Items ingested via the normal checkpoint path are never seen by the probe's
    insert loop, so ``is_major_tech_news`` stays NULL on them. This pass fills
    that gap by querying recently ingested items from trusted sources and running
    the same two-stage classifier used in the probe.
    """
    if not sources:
        return {"classified": 0, "major": 0}

    cutoff = now - timedelta(hours=lookback_hours)
    candidates = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.source.in_(sources),
            ContentItem.is_major_tech_news.is_(None),
            ContentItem.published_at >= cutoff,
            ContentItem.curation_status.in_([ContentStatus.CANDIDATE, ContentStatus.PROMOTED]),
        )
        .order_by(ContentItem.published_at.desc())
        .limit(max_items)
        .all()
    )

    if not candidates:
        return {"classified": 0, "major": 0}

    classified = 0
    major_ids: list[int] = []

    for item in candidates:
        value: dict[str, Any] = {
            "title": item.title or "",
            "description": item.description or item.summary or "",
            "content_text": item.content_text or "",
            "source": item.source or "",
            "source_url": item.source_url or "",
        }
        is_major = _maybe_classify_major_news_value(value, llm_client=llm_client)

        item.is_major_tech_news = value.get("is_major_tech_news", False)
        item.major_tech_news_confidence = value.get("major_tech_news_confidence")
        item.major_tech_news_reason = value.get("major_tech_news_reason")
        # Backfill tech-relevance fields if missing
        if item.tech_relevance is None and value.get("tech_relevance") is not None:
            item.tech_relevance = value.get("tech_relevance")
        if (
            item.tech_relevance_confidence is None
            and value.get("tech_relevance_confidence") is not None
        ):
            item.tech_relevance_confidence = value.get("tech_relevance_confidence")

        classified += 1
        if is_major:
            major_ids.append(int(item.id))

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("[major_news_probe] retro_classify commit failed: %s", exc)
        return {"classified": 0, "major": 0}

    if major_ids:
        _fast_track_major_news_events(
            db, content_item_ids=major_ids, now=datetime.now(timezone.utc)
        )
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning("[major_news_probe] retro_classify fast-track commit failed: %s", exc)

    logger.info(
        "[major_news_probe] retro_classify sources=%s classified=%d major=%d",
        sources,
        classified,
        len(major_ids),
    )
    return {"classified": classified, "major": len(major_ids)}


def run_major_news_probe_job() -> dict[str, Any]:
    """Probe validated major-news feeds within strict feed/item/time bounds."""
    max_seconds = _int_env("MAJOR_NEWS_PROBE_MAX_SECONDS", 60)
    max_feeds = _int_env("MAJOR_NEWS_PROBE_MAX_FEEDS", 6)
    max_inserted = _int_env("MAJOR_NEWS_PROBE_MAX_INSERTED", 20)
    max_entries_per_feed = _int_env("MAJOR_NEWS_PROBE_ENTRIES_PER_FEED", 12)
    max_entry_age_hours = _int_env("MAJOR_NEWS_PROBE_MAX_ENTRY_AGE_HOURS", 48)

    if memory_over_soft_limit():
        _record_major_news_probe_skip(
            status="skipped_memory",
            reason="worker memory is above soft limit",
        )
        result = {
            "status": "skipped_memory",
            "skip_reason": "memory_pressure",
            "worker_memory_mb": current_worker_memory_mb(),
            "soft_limit_mb": memory_soft_limit_mb(),
        }
        logger.warning("[major_news_probe] %s", result)
        return result

    if _ingestion_lane_recently_running():
        _record_major_news_probe_skip(
            status="deferred_ingestion_running",
            reason="ingestion lane is currently running",
        )
        result = {
            "status": "deferred_ingestion_running",
            "skip_reason": "ingestion_running",
        }
        logger.info("[major_news_probe] %s", result)
        return result

    started = time.monotonic()
    deadline = started + max_seconds
    now = datetime.utcnow()
    day_utc = datetime.now(timezone.utc).date()
    feeds = get_feeds_by_role(FeedRole.MAJOR_NEWS)[:max_feeds]
    if not feeds:
        return {"status": "no_feeds", "feeds": 0, "inserted": 0}

    db = SessionLocal()
    rss_client = RSSClient(feed_configs=feeds)
    state_repo = SourceFetchStateRepository(db)
    article_hydrator = ArticleHydrationService()
    llm_client = LLMClient()
    inserted_ids: list[int] = []
    fetched_feeds = 0
    skipped_cooldown = 0
    skipped_stale = 0
    attempted_entries = 0
    errors: list[str] = []

    try:
        for feed in feeds:
            if time.monotonic() >= deadline or len(inserted_ids) >= max_inserted:
                break

            cooldown = state_repo.get_active_cooldown(
                source_type=MAJOR_NEWS_SOURCE_TYPE,
                feed_name=feed.name,
                now=now,
            )
            if cooldown is not None:
                skipped_cooldown += 1
                logger.info(
                    "[major_news_probe] skipping feed=%s cooldown_until=%s",
                    feed.name,
                    cooldown.cooldown_until,
                )
                continue

            try:
                entries = rss_client.fetch_feed(feed.url, max_entries=max_entries_per_feed)
                outcome = rss_client.get_last_fetch_outcome(feed.url)
                if outcome is not None:
                    state_repo.record_outcome(
                        source_type=MAJOR_NEWS_SOURCE_TYPE,
                        feed_name=feed.name,
                        source_url=feed.url,
                        outcome=outcome,
                        now=now,
                    )
                    if not outcome.succeeded:
                        continue

                fetched_feeds += 1
                values: list[dict] = []
                for entry in entries:
                    if (
                        time.monotonic() >= deadline
                        or len(inserted_ids) + len(values) >= max_inserted
                    ):
                        break
                    attempted_entries += 1
                    entry.feed_name = feed.name
                    entry.feed_role = feed.role
                    entry.quality_tier = feed.quality_tier
                    entry.decay_profile = feed.decay_profile
                    entry.base_quality_weight = feed.base_quality_weight
                    value = _entry_to_value(
                        entry,
                        feed_name=feed.name,
                        day_utc=day_utc,
                        article_hydrator=article_hydrator,
                    )
                    if value is not None:
                        if _is_stale_for_major_news_probe(
                            value,
                            now=now,
                            max_age_hours=max_entry_age_hours,
                        ):
                            skipped_stale += 1
                            continue
                        _maybe_classify_major_news_value(value, llm_client=llm_client)
                        values.append(value)

                ids = _insert_content_items_postgres(db, values=values)
                if ids:
                    inserted_ids.extend(ids)
                    _queue_followup_events_for_inserted_ids(db, inserted_ids=ids)
                    major_ids = [
                        int(item.id)
                        for item in db.query(ContentItem)
                        .filter(
                            ContentItem.id.in_(ids),
                            ContentItem.is_major_tech_news.is_(True),
                        )
                        .all()
                    ]
                    _fast_track_major_news_events(db, content_item_ids=major_ids, now=now)
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                errors.append(f"{feed.name}: {exc}")
                logger.warning("[major_news_probe] feed=%s failed: %s", feed.name, exc)

        # Retroactively classify items from premium BREAKING sources that the
        # probe's insert path cannot reach (on_conflict_do_nothing means the
        # is_major_tech_news flag is never set for pre-existing items).
        retro_max = _int_env("MAJOR_NEWS_RETRO_CLASSIFY_MAX", 15)
        retro_lookback = _int_env("MAJOR_NEWS_RETRO_CLASSIFY_HOURS", 24)
        retro_stats = _retro_classify_premium_breaking(
            db,
            llm_client,
            sources=MAJOR_NEWS_RETRO_CLASSIFY_SOURCES,
            max_items=retro_max,
            lookback_hours=retro_lookback,
            now=now,
        )

        result = {
            "status": "ok" if not errors else "partial",
            "feeds": len(feeds),
            "fetched_feeds": fetched_feeds,
            "skipped_cooldown": skipped_cooldown,
            "skipped_stale": skipped_stale,
            "max_entry_age_hours": max_entry_age_hours,
            "attempted_entries": attempted_entries,
            "inserted": len(inserted_ids),
            "inserted_ids": inserted_ids[:20],
            "retro_classified": retro_stats["classified"],
            "retro_major": retro_stats["major"],
            "duration_seconds": round(time.monotonic() - started, 2),
            "errors": errors[:5],
        }
        logger.info("[major_news_probe] %s", result)
        return result
    finally:
        db.close()
