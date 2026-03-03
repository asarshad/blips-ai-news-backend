"""AI summarization for ingested content.

The primary entry-point is ``process_ai_summaries`` which picks up every
content item with ``ai_processed=False`` and runs the LLM pipeline on it.

``retry_ai_processing`` is an alias kept for backward-compatibility with the
APScheduler job registration.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

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
        from app.integrations.llm_client import LLMClient
        from app.models.content import ContentType
        from app.repositories.content_repo import ContentItemRepository

        content_repo = ContentItemRepository(db)
        llm_client = LLMClient()

        if not llm_client.is_configured():
            logger.warning(
                f"[ai_retry] {llm_client.get_provider()} API key not configured, skipping"
            )
            return

        items = content_repo.get_unprocessed_by_ai(limit=MAX_ITEMS_PER_RUN, hours_back=168)
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
                    result = llm_client.summarize_article(item.title, text)
                    summary = result.summary
                    topics = result.tags if result.tags else item.topics
                    starters = result.conversation_starters
                else:
                    result = llm_client.summarize_video(item.title, text)
                    summary = result.summary
                    topics = item.topics
                    starters = result.conversation_starters

                stats.llm_calls += 1

                if summary and len(summary.strip()) > 50:
                    content_repo.mark_ai_processed(item.id, summary=summary, topics=topics)
                    # Persist starters from the same LLM call when available
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

        # Second pass: generate conversation starters for items that have summaries but no starters
        _backfill_starters(db, llm_client, stats)

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[ai_retry] Fatal error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


# Backward-compatible alias so the scheduler job keeps working.
retry_ai_processing = process_ai_summaries


def _backfill_starters(db: Session, llm_client, stats) -> None:
    """Backfill conversation starters for items that have summaries but no starters.

    This ensures the feed API returns inline starters, eliminating the need
    for the mobile app to make a separate /starters/{id} API call.
    """
    from app.models.content import ContentItem, ContentType
    from app.services.conversation_starters import get_starters_service

    if not llm_client.is_configured():
        return

    # Find items with summaries but no conversation_starters (limit to avoid LLM cost spikes)
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
