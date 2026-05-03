"""AI summarization for ingested content.

The primary entry-point is ``process_ai_summaries`` which picks up every
content item with ``ai_processed=False`` and runs the LLM pipeline on it.

``retry_ai_processing`` is an alias kept for backward-compatibility with the
APScheduler job registration.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content import ContentType
from app.scheduler.config import LLM_RATE_LIMIT_DELAY, MAX_ITEMS_PER_RUN, MAX_LLM_CALLS_PER_RUN
from app.scheduler.job_stats import log_job_start
from app.scheduler.runtime import (
    AI_RETRY_JOB,
    FETCH_NEWS_INLINE_AI_RETRY,
    log_memory_snapshot,
    mark_job_finished,
    mark_job_started,
)
from app.services.ai_retry_state import (
    article_retry_state,
    record_article_retry_deferral,
    record_video_summary_failure,
    video_summary_retry_state,
)
from app.services.article_unskimmable_service import (
    is_terminal_unskimmable_article,
    reject_terminal_unskimmable_article,
)

logger = get_logger(__name__)


def _effective_item_limit(max_items: int | None) -> int:
    if max_items is not None:
        return max(1, int(max_items))
    return max(MAX_ITEMS_PER_RUN, int(settings.ARTICLE_AI_PRIORITY_MAX_ITEMS_PER_RUN))


def _effective_llm_call_cap() -> int:
    return max(MAX_LLM_CALLS_PER_RUN, int(settings.ARTICLE_AI_PRIORITY_MAX_LLM_CALLS_PER_RUN))


def _normalize_retry_batch(batch) -> list:
    if isinstance(batch, list):
        return batch
    if isinstance(batch, tuple):
        return list(batch)
    return []


def _build_ai_retry_worklist(content_repo, *, item_limit: int):
    """Prioritize recent promoted article/video backlog before other maintenance."""
    if hasattr(content_repo, "get_recent_promoted_articles_pending_ai"):
        recent_article_candidates = _normalize_retry_batch(
            content_repo.get_recent_promoted_articles_pending_ai(
                limit=max(item_limit * 3, item_limit),
                lookback_days=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS,
            )
        )
    else:
        recent_article_candidates = _normalize_retry_batch(
            content_repo.get_unprocessed_by_ai(
                limit=max(item_limit * 3, item_limit),
                hours_back=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS * 24,
            )
        )
    items = _select_retry_eligible_articles(recent_article_candidates, limit=item_limit)

    seen_ids = {int(item.id) for item in items if getattr(item, "id", None) is not None}
    remaining_slots = max(0, item_limit - len(items))

    if remaining_slots:
        if hasattr(content_repo, "get_recent_promoted_videos_pending_ai"):
            recent_video_candidates = _normalize_retry_batch(
                content_repo.get_recent_promoted_videos_pending_ai(
                    limit=max(remaining_slots * 3, remaining_slots),
                    lookback_hours=168,
                )
            )
        else:
            recent_video_candidates = [
                item
                for item in _normalize_retry_batch(
                    content_repo.get_unprocessed_by_ai(
                        limit=max(remaining_slots * 3, remaining_slots),
                        hours_back=168,
                    )
                )
                if getattr(item, "type", None) == ContentType.VIDEO
            ]

        for item in _select_retry_eligible_videos(recent_video_candidates):
            item_id = getattr(item, "id", None)
            if item_id is not None and int(item_id) in seen_ids:
                continue
            items.append(item)
            if item_id is not None:
                seen_ids.add(int(item_id))
            if len(items) >= item_limit:
                return items

    remaining_slots = max(0, item_limit - len(items))
    if remaining_slots:
        for batch in (
            _normalize_retry_batch(
                content_repo.get_articles_with_short_summaries(
                    limit=remaining_slots,
                    hours_back=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS * 24,
                    max_words=settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS,
                )
            ),
            _normalize_retry_batch(
                content_repo.get_articles_with_long_summaries(
                    limit=remaining_slots,
                    hours_back=None,
                    min_words=settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS,
                )
            ),
            _normalize_retry_batch(
                content_repo.get_videos_with_short_summaries(
                    limit=remaining_slots,
                    hours_back=168,
                    min_words=settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS,
                )
            ),
        ):
            for item in _select_retry_eligible_videos(batch):
                item_id = getattr(item, "id", None)
                if item_id is not None and int(item_id) in seen_ids:
                    continue
                items.append(item)
                if item_id is not None:
                    seen_ids.add(int(item_id))
                if len(items) >= item_limit:
                    return items
            remaining_slots = max(0, item_limit - len(items))
            if remaining_slots <= 0:
                return items

    return items


def _select_retry_eligible_articles(items, *, limit: int):
    now = datetime.utcnow()
    eligible = []
    for item in items:
        state = _article_retry_state(item, now=now)
        if state["eligible"] or not is_terminal_unskimmable_article(item):
            eligible.append(item)
        if len(eligible) >= limit:
            break
    return eligible


def _select_retry_eligible_videos(items):
    now = datetime.utcnow()
    eligible = []
    for item in items:
        if getattr(item, "type", None) != ContentType.VIDEO:
            eligible.append(item)
            continue
        if video_summary_retry_state(item, now=now)["eligible"]:
            eligible.append(item)
    return eligible


def _is_recent_article_for_maintenance(item, *, now: datetime | None = None) -> bool:
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return False
    now_utc = now or datetime.utcnow()
    return published_at >= now_utc - timedelta(days=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS)


def _allow_summary_rescue_for_item(item, *, now: datetime | None = None) -> bool:
    """Allow full-model rescue only for recent promoted, unsuppressed content."""
    from app.models.content import ContentStatus

    if getattr(item, "curation_status", None) != ContentStatus.PROMOTED:
        return False
    if bool(getattr(item, "is_suppressed", False)):
        return False
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return False

    now_utc = now or datetime.utcnow()
    lookback_days = (
        settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS
        if getattr(item, "type", None) == ContentType.ARTICLE
        else 7
    )
    return published_at >= now_utc - timedelta(days=lookback_days)


def _article_retry_state(item, *, now: datetime | None = None) -> dict[str, object]:
    return article_retry_state(item, now=now)


def _record_article_retry_deferral(db: Session, item, *, now: datetime | None = None) -> int:
    from app.services.content_readiness import seed_content_readiness

    attempts = record_article_retry_deferral(item, now=now)
    seed_content_readiness(item, now=now)
    db.commit()
    return attempts


def _reject_terminal_unskimmable_article(db: Session, item, *, reason: str) -> None:
    reject_terminal_unskimmable_article(db, item, reason=reason)
    db.commit()


def _record_video_summary_failure(db: Session, item, *, reason: str) -> dict[str, object]:
    from app.services.content_readiness import seed_content_readiness

    state = record_video_summary_failure(item, reason=reason)
    seed_content_readiness(item)
    db.commit()
    logger.warning(
        "[ai_retry] Video summary failure attempt=%s terminal=%s id=%s title=%s reason=%s",
        state["attempts"],
        state["terminal"],
        getattr(item, "id", None),
        (getattr(item, "title", "") or "")[:80],
        reason,
    )
    return state


def process_ai_summaries(
    *,
    max_items: int | None = None,
    include_maintenance: bool = True,
    trigger: str = "manual",
):
    """Summarise all unprocessed content items via the LLM pipeline.

    Called:
    - Immediately after each ingestion run (event-driven).
    - Every 15 min by the scheduler as a safety-net.
    """

    if not feature_flags.is_enabled("summarization"):
        logger.info("[ai_retry] SKIPPED - summarization feature is disabled")
        return

    stats = log_job_start("ai_retry")
    job_key = FETCH_NEWS_INLINE_AI_RETRY if trigger == "fetch_news" else AI_RETRY_JOB
    run_started_at = None
    run_success = False
    db = None

    try:
        run_started_at = mark_job_started(job_key)
        log_memory_snapshot(logger, f"{job_key}:start")
        db = SessionLocal()
        from app.article_hydration import ArticleHydrationService, bounded_article_summary_text
        from app.integrations import LLMClient
        from app.integrations.llm_client import (
            is_video_summary_acceptable,
            normalize_video_summary_output,
        )
        from app.models.content import ContentType
        from app.repositories.content_repo import ContentItemRepository
        from app.services.content_readiness import sync_content_readiness
        from app.services.playlist_service import refresh_cached_playlist_items
        from app.services.tiered_feed_service import invalidate_tiered_feed_cache
        from app.services.video_relevance_service import classify_video_blips_relevance

        content_repo = ContentItemRepository(db)
        llm_client = LLMClient()
        article_hydrator = ArticleHydrationService(llm_client=llm_client)
        touched_content_ids: set[int] = set()

        if not llm_client.is_configured():
            logger.warning(
                f"[ai_retry] {llm_client.get_provider()} API key not configured, skipping"
            )
            return

        item_limit = _effective_item_limit(max_items)
        llm_call_cap = _effective_llm_call_cap()
        items = _build_ai_retry_worklist(content_repo, item_limit=item_limit)

        if not items:
            logger.info("[ai_retry] No items need processing")
            return

        logger.info(f"[ai_retry] Found {len(items)} items to process")

        for item in items:
            if stats.llm_calls >= llm_call_cap:
                logger.warning(f"[ai_retry] LLM cap reached ({llm_call_cap}), stopping")
                stats.items_skipped = len(items) - stats.items_processed - stats.items_failed
                break

            try:
                if item.type == ContentType.REEL:
                    content_repo.mark_ai_processed(
                        item.id,
                        summary="",
                        topics=item.topics or [],
                        commit=False,
                    )
                    db.commit()
                    touched_content_ids.add(int(item.id))
                    stats.items_processed += 1
                    continue

                text = item.content_text or item.description or item.title

                if item.type == ContentType.ARTICLE:
                    needs_retry_refresh = any(
                        (
                            not (item.content_text or "").strip(),
                            not (getattr(item, "image_url", None) or "").strip(),
                            not (getattr(item, "canonical_url", None) or "").strip(),
                        )
                    )
                    if needs_retry_refresh:
                        _refresh_article_retry_inputs(article_hydrator, item)
                    text = item.content_text or item.description or item.title
                    previous_summary = item.summary
                    item.ai_processed = False
                    item.summary = None

                    summary_input = bounded_article_summary_text(text)
                    if not summary_input:
                        if _is_recent_article_for_maintenance(item):
                            item.summary = previous_summary
                            attempt = _record_article_retry_deferral(db, item)
                            if attempt >= int(settings.ARTICLE_UNSKIMMABLE_RETRY_MAX_ATTEMPTS):
                                _reject_terminal_unskimmable_article(
                                    db,
                                    item,
                                    reason="max_unskimmable_attempts",
                                )
                                touched_content_ids.add(int(item.id))
                                stats.items_processed += 1
                                logger.info(
                                    "[ai_retry] Terminally rejected unskimmable article after attempt %s: %s",
                                    attempt,
                                    item.title[:80],
                                )
                                continue
                            logger.info(
                                "[ai_retry] Deferred unskimmable article retry attempt %s: %s",
                                attempt,
                                item.title[:80],
                            )
                        else:
                            _reject_terminal_unskimmable_article(
                                db,
                                item,
                                reason="outside_retry_lookback",
                            )
                            touched_content_ids.add(int(item.id))
                            logger.info(
                                "[ai_retry] Terminally rejected older unskimmable article: %s",
                                item.title[:80],
                            )
                            stats.items_processed += 1
                            continue
                        stats.items_skipped += 1
                        continue

                    article_hydrator.populate_article_summary(
                        item,
                        precompute_starter_answers=False,
                        allow_summary_rescue=_allow_summary_rescue_for_item(item),
                    )
                    article_hydrator.refresh_article_annotations(item)
                    summary = item.summary
                    topics = item.topics
                    starters = item.conversation_starters
                else:
                    relevance = classify_video_blips_relevance(
                        llm_client,
                        title=item.title or "",
                        summary=text or "",
                        source=getattr(item, "source", None) or "",
                        url=getattr(item, "source_url", None) or None,
                    )
                    if settings.VIDEO_TECH_CLASSIFIER_ENABLED:
                        stats.llm_calls += 1
                    if relevance is not None and not relevance.is_relevant:
                        summary = None
                        topics = item.topics
                        starters = None
                        item.tech_relevance = "none"
                        item.tech_relevance_confidence = relevance.confidence
                        item.tech_relevance_reason = relevance.reason
                        item.is_mixed_roundup = False
                    else:
                        try:
                            result = llm_client.summarize_video(
                                item.title,
                                text,
                                allow_rescue=_allow_summary_rescue_for_item(item),
                            )
                        except ValueError as exc:
                            state = _record_video_summary_failure(
                                db,
                                item,
                                reason=str(exc) or "empty_summary_exception",
                            )
                            if state["terminal"]:
                                touched_content_ids.add(int(item.id))
                                stats.items_processed += 1
                            else:
                                stats.items_skipped += 1
                            continue
                        summary = normalize_video_summary_output(result.summary)
                        topics = item.topics
                        starters = result.conversation_starters
                        item.tech_relevance = result.tech_relevance
                        item.tech_relevance_confidence = result.tech_relevance_confidence
                        item.tech_relevance_reason = result.tech_relevance_reason
                        item.is_mixed_roundup = result.is_mixed_roundup
                        stats.llm_calls += 1

                if item.type == ContentType.ARTICLE:
                    stats.llm_calls += 1

                classification_only = item.type == ContentType.VIDEO and (
                    getattr(item, "tech_relevance", None) == "none"
                    or bool(getattr(item, "is_mixed_roundup", False))
                )
                summary_is_valid = (
                    bool(summary and len(summary.strip()) > 50)
                    if item.type == ContentType.ARTICLE
                    else is_video_summary_acceptable(summary)
                )
                if summary_is_valid:
                    content_repo.mark_ai_processed(
                        item.id,
                        summary=summary,
                        topics=topics,
                        commit=False,
                    )
                    touched_content_ids.add(int(item.id))
                    if starters and not item.conversation_starters:
                        item.conversation_starters = starters
                    if starters and not getattr(item, "starter_answers", None):
                        from app.services.conversation_starters import get_starters_service

                        if stats.llm_calls >= llm_call_cap:
                            logger.warning(
                                "[ai_retry] Starter-answer generation skipped for %s because the LLM cap was reached",
                                item.id,
                            )
                        else:
                            try:
                                if get_starters_service(llm_client).generate_answers_and_persist(
                                    item
                                ):
                                    stats.llm_calls += 1
                            except Exception as exc:
                                logger.warning(
                                    "[ai_retry] Starter-answer generation failed for %s: %s",
                                    item.id,
                                    exc,
                                )
                    db.commit()
                    stats.items_processed += 1
                elif classification_only:
                    item.ai_processed = True
                    item.summary = None
                    sync_content_readiness(db, item)
                    db.commit()
                    touched_content_ids.add(int(item.id))
                    stats.items_processed += 1
                else:
                    state = _record_video_summary_failure(
                        db,
                        item,
                        reason="empty_or_short_summary",
                    )
                    if state["terminal"]:
                        touched_content_ids.add(int(item.id))
                        stats.items_processed += 1
                    else:
                        stats.items_skipped += 1

                time.sleep(LLM_RATE_LIMIT_DELAY)

            except Exception as e:
                stats.items_failed += 1
                stats.errors.append(f"{item.title[:50]}: {str(e)}")
                continue

        if touched_content_ids:
            invalidate_tiered_feed_cache()
            cache_refresh = refresh_cached_playlist_items(
                db,
                content_ids=sorted(touched_content_ids),
            )
            logger.info("[ai_retry] Refreshed playlist caches: %s", cache_refresh)

        if include_maintenance:
            _backfill_starters(db, llm_client, stats)
            _backfill_starter_answers(db, llm_client, stats)
            # Article image verification now runs on its own dedicated
            # scheduler (see tasks_article_image.py) so ai_retry no longer
            # piggybacks it here — that coupling previously bottlenecked
            # image recovery on the 15-min LLM cadence.
        else:
            logger.info("[ai_retry] Maintenance skipped for this run")
        run_success = not stats.errors

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[ai_retry] Fatal error: {str(e)}")
    finally:
        log_memory_snapshot(logger, f"{job_key}:finished")
        if run_started_at is not None:
            mark_job_finished(job_key, run_started_at, success=run_success)
        if db is not None:
            db.close()
        stats.complete()
        stats.log_summary()


retry_ai_processing = process_ai_summaries


def _refresh_article_retry_inputs(article_hydrator, item) -> bool:
    """Refresh article extraction fields before retrying summarization."""
    extraction = article_hydrator.run_article_extraction(item)
    if extraction is None:
        return False

    changed = False

    extracted_title = (getattr(extraction, "title", None) or "").strip()
    if extracted_title and extracted_title != (item.title or "").strip():
        item.title = extracted_title
        changed = True

    extracted_canonical = (getattr(extraction, "canonical_url", None) or "").strip()
    if extracted_canonical and extracted_canonical != (item.canonical_url or "").strip():
        item.canonical_url = extracted_canonical
        changed = True

    extracted_published_at = getattr(extraction, "published_at", None)
    if extracted_published_at is not None and extracted_published_at != getattr(
        item, "published_at", None
    ):
        item.published_at = extracted_published_at
        changed = True

    extracted_text = (
        getattr(extraction, "main_text", None)
        or getattr(extraction, "excerpt_fallback", None)
        or ""
    ).strip()
    if extracted_text and extracted_text != (item.content_text or "").strip():
        item.content_text = extracted_text[:8000]
        changed = True

    extracted_image = (getattr(extraction, "image_url", None) or "").strip()
    if article_hydrator.should_replace_article_image(
        getattr(item, "image_url", None),
        extracted_image,
        candidate_source="extraction",
    ):
        item.image_url = extracted_image
        changed = True

    if changed:
        article_hydrator.refresh_article_identity(item)

    return changed


def _backfill_starters(db: Session, llm_client, stats) -> None:
    """Backfill conversation starters for items that have summaries but no starters.

    This ensures the feed API returns inline starters, eliminating the need
    for the mobile app to make a separate /starters/{id} API call.
    """
    from app.models.content import ContentItem, ContentType
    from app.services.conversation_starters import get_starters_service

    if not llm_client.is_configured():
        return

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.ai_processed.is_(True),
            ContentItem.conversation_starters.is_(None),
            ContentItem.type.in_([ContentType.ARTICLE, ContentType.VIDEO]),
        )
        .order_by(ContentItem.created_at.desc())
        .limit(20)
        .all()
    )

    if not items:
        return

    logger.info(f"[ai_retry] Backfilling starters for {len(items)} items")
    starters_service = get_starters_service(llm_client)

    llm_call_cap = _effective_llm_call_cap()

    for item in items:
        if stats.llm_calls >= llm_call_cap:
            break
        try:
            starters_service.generate_and_persist(item)
            db.commit()
            stats.llm_calls += 1
            time.sleep(LLM_RATE_LIMIT_DELAY)
        except Exception as e:
            db.rollback()
            logger.warning(f"[ai_retry] Starters backfill failed for {item.id}: {e}")


def _backfill_starter_answers(db: Session, llm_client, stats) -> None:
    """Backfill starter answers for items that already have starters."""
    from app.models.content import ContentItem, ContentType
    from app.services.conversation_starters import get_starters_service

    if not llm_client.is_configured():
        return

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.ai_processed.is_(True),
            ContentItem.conversation_starters.isnot(None),
            ContentItem.starter_answers.is_(None),
            ContentItem.type.in_([ContentType.ARTICLE, ContentType.VIDEO]),
        )
        .order_by(ContentItem.created_at.desc())
        .limit(20)
        .all()
    )

    if not items:
        return

    logger.info(f"[ai_retry] Backfilling starter answers for {len(items)} items")
    starters_service = get_starters_service(llm_client)

    llm_call_cap = _effective_llm_call_cap()

    for item in items:
        if stats.llm_calls >= llm_call_cap:
            break
        try:
            if starters_service.generate_answers_and_persist(item):
                db.commit()
                stats.llm_calls += 1
                time.sleep(LLM_RATE_LIMIT_DELAY)
        except Exception as e:
            db.rollback()
            logger.warning(f"[ai_retry] Starter-answer backfill failed for {item.id}: {e}")
