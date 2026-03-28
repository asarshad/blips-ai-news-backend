"""AI summarization for ingested content.

The primary entry-point is ``process_ai_summaries`` which picks up every
content item with ``ai_processed=False`` and runs the LLM pipeline on it.

``retry_ai_processing`` is an alias kept for backward-compatibility with the
APScheduler job registration.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.config import LLM_RATE_LIMIT_DELAY, MAX_ITEMS_PER_RUN, MAX_LLM_CALLS_PER_RUN
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def process_ai_summaries():
    """Summarise all unprocessed content items via the LLM pipeline.

    Called:
    - Immediately after each ingestion run (event-driven).
    - Every 15 min by the scheduler as a safety-net.
    """

    if not feature_flags.is_enabled("summarization"):
        logger.info("[ai_retry] SKIPPED - summarization feature is disabled")
        return

    stats = log_job_start("ai_retry")

    db = SessionLocal()
    try:
        from app.article_hydration import ArticleHydrationService, bounded_article_summary_text
        from app.integrations import LLMClient
        from app.integrations.llm_client import (
            is_video_summary_acceptable,
            normalize_video_summary_output,
        )
        from app.models.content import ContentType
        from app.repositories.content_repo import ContentItemRepository

        content_repo = ContentItemRepository(db)
        llm_client = LLMClient()
        article_hydrator = ArticleHydrationService(llm_client=llm_client)

        if not llm_client.is_configured():
            logger.warning(
                f"[ai_retry] {llm_client.get_provider()} API key not configured, skipping"
            )
            return

        items = content_repo.get_unprocessed_by_ai(limit=MAX_ITEMS_PER_RUN, hours_back=168)
        remaining_slots = max(0, MAX_ITEMS_PER_RUN - len(items))
        if remaining_slots:
            items.extend(
                content_repo.get_articles_with_short_summaries(
                    limit=remaining_slots,
                    hours_back=168,
                    max_words=settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS,
                )
            )
        remaining_slots = max(0, MAX_ITEMS_PER_RUN - len(items))
        if remaining_slots:
            items.extend(
                content_repo.get_articles_with_long_summaries(
                    limit=remaining_slots,
                    hours_back=None,
                    min_words=settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS,
                )
            )
        remaining_slots = max(0, MAX_ITEMS_PER_RUN - len(items))
        if remaining_slots:
            items.extend(
                content_repo.get_videos_with_short_summaries(
                    limit=remaining_slots,
                    hours_back=168,
                    min_words=settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS,
                )
            )

        if not items:
            logger.info("[ai_retry] No items need processing")
            return

        logger.info(f"[ai_retry] Found {len(items)} items to process")

        for item in items:
            if stats.llm_calls >= MAX_LLM_CALLS_PER_RUN:
                logger.warning(f"[ai_retry] LLM cap reached ({MAX_LLM_CALLS_PER_RUN}), stopping")
                stats.items_skipped = len(items) - stats.items_processed - stats.items_failed
                break

            try:
                if item.type == ContentType.REEL:
                    content_repo.mark_ai_processed(item.id, summary="", topics=item.topics or [])
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
                    item.ai_processed = False
                    item.summary = None

                    summary_input = bounded_article_summary_text(text)
                    if not summary_input:
                        # No scrapeable content found even after a refresh attempt.
                        # Permanently mark as processed with empty summary so the item
                        # is removed from the retry queue and stays PENDING in readiness
                        # (missing_article_summary) rather than re-entering every cycle.
                        content_repo.mark_ai_processed(
                            item.id, summary="", topics=item.topics or []
                        )
                        stats.items_skipped += 1
                        logger.info(
                            "[ai_retry] Giving up on unskimmable article (marked processed): %s",
                            item.title[:80],
                        )
                        continue

                    article_hydrator.populate_article_summary(item)
                    article_hydrator.refresh_article_annotations(item)
                    summary = item.summary
                    topics = item.topics
                    starters = item.conversation_starters
                else:
                    result = llm_client.summarize_video(item.title, text)
                    summary = normalize_video_summary_output(result.summary)
                    topics = item.topics
                    starters = result.conversation_starters

                stats.llm_calls += 1

                summary_is_valid = (
                    bool(summary and len(summary.strip()) > 50)
                    if item.type == ContentType.ARTICLE
                    else is_video_summary_acceptable(summary)
                )
                if summary_is_valid:
                    content_repo.mark_ai_processed(item.id, summary=summary, topics=topics)
                    if starters and not item.conversation_starters:
                        item.conversation_starters = starters
                        db.commit()
                    stats.items_processed += 1
                else:
                    stats.items_failed += 1
                    stats.errors.append(f"Empty summary: {item.title[:50]}")

                time.sleep(LLM_RATE_LIMIT_DELAY)

            except Exception as e:
                stats.items_failed += 1
                stats.errors.append(f"{item.title[:50]}: {str(e)}")
                continue

        _backfill_starters(db, llm_client, stats)
        image_repair = _run_article_image_verification(db)
        logger.info("[ai_retry] Article image verification: %s", image_repair)
        from app.scheduler.tasks_content_events import run_content_event_dispatch_job

        run_content_event_dispatch_job()

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[ai_retry] Fatal error: {str(e)}")
    finally:
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

    for item in items:
        if stats.llm_calls >= MAX_LLM_CALLS_PER_RUN:
            break
        try:
            starters_service.generate_and_persist(item)
            db.commit()
            stats.llm_calls += 1
            time.sleep(LLM_RATE_LIMIT_DELAY)
        except Exception as e:
            db.rollback()
            logger.warning(f"[ai_retry] Starters backfill failed for {item.id}: {e}")


def _run_article_image_verification(db: Session) -> dict[str, int]:
    """Run the lightweight article image verification pass for recent promoted rows."""
    from app.services.article_image_service import repair_article_image_metadata

    return repair_article_image_metadata(
        db,
        lookback_days=3,
        limit=max(50, MAX_ITEMS_PER_RUN),
        include_generic=True,
    )
