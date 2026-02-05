"""AI processing retry scheduled task."""

from __future__ import annotations

import time

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.config import LLM_RATE_LIMIT_DELAY, MAX_ITEMS_PER_RUN, MAX_LLM_CALLS_PER_RUN
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def retry_ai_processing():
    """Retry AI processing for content that failed previously."""

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
            logger.warning(f"[ai_retry] {llm_client.get_provider()} API key not configured, skipping")
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

                text = item.description or item.title

                if item.type == ContentType.ARTICLE:
                    result = llm_client.summarize_article(item.title, text)
                    summary = result.summary
                    topics = result.tags if result.tags else item.topics
                else:
                    summary = llm_client.summarize_video(item.title, text)
                    topics = item.topics

                stats.llm_calls += 1

                if summary and len(summary.strip()) > 50:
                    content_repo.mark_ai_processed(item.id, summary=summary, topics=topics)
                    stats.items_processed += 1
                else:
                    stats.items_failed += 1
                    stats.errors.append(f"Empty summary: {item.title[:50]}")

                time.sleep(LLM_RATE_LIMIT_DELAY)

            except Exception as e:
                stats.items_failed += 1
                stats.errors.append(f"{item.title[:50]}: {str(e)}")
                continue

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[ai_retry] Fatal error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
