"""Event-driven AI summarization helpers for promoted content."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.article_hydration import ArticleHydrationService, bounded_article_summary_text
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import LLMClient
from app.integrations.llm_client import (
    is_video_summary_acceptable,
    normalize_video_summary_output,
)
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.repositories.content_repo import ContentItemRepository
from app.services.content_readiness import sync_content_readiness
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

logger = get_logger(__name__)

CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE = "content.ai_summary.requested"
_ARTICLE_RETRY_SENTINEL_PREFIX = "__blips_article_retry__"


def should_queue_content_ai_summary(item: Any) -> bool:
    """Return True when promoted content should be queued for AI summarization."""
    if getattr(item, "id", None) is None:
        return False
    if getattr(item, "type", None) not in (ContentType.ARTICLE, ContentType.VIDEO):
        return False
    if getattr(item, "curation_status", None) != ContentStatus.PROMOTED:
        return False
    if bool(getattr(item, "is_suppressed", False)):
        return False
    return not bool(getattr(item, "ai_processed", False))


def queue_content_ai_summary_request(
    db: Session,
    item: Any,
    *,
    now: Optional[datetime] = None,
) -> ContentEventOutbox | None:
    """Enqueue a durable AI-summary request for one content item."""
    if not should_queue_content_ai_summary(item):
        return None

    existing = (
        db.query(ContentEventOutbox.id)
        .filter(
            ContentEventOutbox.content_item_id == int(item.id),
            ContentEventOutbox.event_type == CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
            ContentEventOutbox.status.in_(("pending", "processing")),
        )
        .first()
    )
    if existing is not None:
        return None

    event = ContentEventOutbox(
        content_item_id=int(item.id),
        event_type=CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
        payload={
            "content_id": int(item.id),
            "content_type": getattr(
                getattr(item, "type", None),
                "value",
                getattr(item, "type", None),
            ),
            "readiness_reason": (getattr(item, "readiness_reason", None) or "").strip() or None,
        },
        status="pending",
        available_at=now or datetime.utcnow(),
    )
    db.add(event)
    return event


def process_content_ai_summary_request(
    db: Session,
    *,
    content_id: int,
    llm_client: Optional[LLMClient] = None,
    article_hydrator: Optional[ArticleHydrationService] = None,
) -> Dict[str, Any]:
    """Process one event-driven AI summary request."""
    item = db.get(ContentItem, int(content_id))
    if item is None:
        logger.info("[content_ai] skipping missing content_id=%s", content_id)
        return {
            "content_id": int(content_id),
            "changed": False,
            "skipped": True,
            "reason": "missing_content",
        }

    if item.type not in (ContentType.ARTICLE, ContentType.VIDEO):
        logger.info(
            "[content_ai] skipping unsupported type content_id=%s type=%s",
            item.id,
            item.type,
        )
        return {
            "content_id": int(item.id),
            "changed": False,
            "skipped": True,
            "reason": "unsupported_type",
            "content_type": item.type.value,
        }

    if item.curation_status != ContentStatus.PROMOTED or item.is_suppressed:
        logger.info(
            "[content_ai] skipping content_id=%s status=%s suppressed=%s",
            item.id,
            getattr(item.curation_status, "value", item.curation_status),
            bool(item.is_suppressed),
        )
        return {
            "content_id": int(item.id),
            "changed": False,
            "skipped": True,
            "reason": "not_promoted_or_suppressed",
            "content_type": item.type.value,
        }

    if item.ai_processed:
        logger.info("[content_ai] skipping already processed content_id=%s", item.id)
        return {
            "content_id": int(item.id),
            "changed": False,
            "skipped": True,
            "reason": "already_processed",
            "content_type": item.type.value,
            "ai_processed": True,
        }

    resolved_llm_client = llm_client or LLMClient()
    if not resolved_llm_client.is_configured():
        raise RuntimeError(
            f"{resolved_llm_client.get_provider()} API key not configured for event-driven AI"
        )

    hydrator = article_hydrator or ArticleHydrationService(llm_client=resolved_llm_client)
    content_repo = ContentItemRepository(db)

    changed = False

    if item.type == ContentType.ARTICLE:
        changed = _process_article_summary(
            db,
            item=item,
            content_repo=content_repo,
            article_hydrator=hydrator,
            llm_client=resolved_llm_client,
        )
    else:
        changed = _process_video_summary(
            db,
            item=item,
            content_repo=content_repo,
            llm_client=resolved_llm_client,
        )

    if changed:
        invalidate_tiered_feed_cache()
        cache_refresh = refresh_cached_playlist_items(db, content_ids=[int(item.id)])
    else:
        cache_refresh = None

    logger.info(
        "[content_ai] processed content_id=%s changed=%s type=%s ai_processed=%s readiness=%s",
        item.id,
        changed,
        item.type.value,
        bool(item.ai_processed),
        getattr(item, "readiness_status", None),
    )

    return {
        "content_id": int(item.id),
        "content_type": item.type.value,
        "changed": changed,
        "skipped": False,
        "ai_processed": bool(item.ai_processed),
        "summary_present": bool((item.summary or "").strip()),
        "readiness_status": (getattr(item, "readiness_status", None) or "").strip() or None,
        "readiness_reason": (getattr(item, "readiness_reason", None) or "").strip() or None,
        "cache_refresh": cache_refresh,
    }


def _process_article_summary(
    db: Session,
    *,
    item: ContentItem,
    content_repo: ContentItemRepository,
    article_hydrator: ArticleHydrationService,
    llm_client: LLMClient,
) -> bool:
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
        if _is_recent_article_for_maintenance(item):
            attempt = _record_article_retry_deferral(db, item)
            logger.info(
                "[content_ai] deferred unskimmable article content_id=%s attempt=%s",
                item.id,
                attempt,
            )
            return True

        content_repo.mark_ai_processed(
            item.id,
            summary="",
            topics=item.topics or [],
            commit=False,
        )
        logger.info(
            "[content_ai] marked older unskimmable article processed content_id=%s",
            item.id,
        )
        return True

    article_hydrator.populate_article_summary(
        item,
        precompute_starter_answers=False,
    )
    article_hydrator.refresh_article_annotations(item)

    summary = item.summary
    topics = item.topics
    starters = item.conversation_starters

    if not bool(summary and len(summary.strip()) > 50):
        logger.warning("[content_ai] invalid article summary content_id=%s", item.id)
        return False

    if settings.ARTICLE_TECH_CLASSIFIER_ENABLED:
        try:
            tech = llm_client.classify_blips_tech_relevance(
                title=item.title or "",
                summary=summary,
                source=item.source or "",
                url=item.source_url or None,
            )
            item.tech_relevance = tech.is_blips_tech_relevant
            item.tech_relevance_confidence = tech.confidence
            item.tech_relevance_reason = tech.reason
            logger.info(
                "[content_ai] article tech relevance content_id=%s relevant=%s confidence=%.2f",
                item.id,
                tech.is_blips_tech_relevant,
                tech.confidence,
            )
        except Exception as exc:
            logger.warning(
                "[content_ai] article tech relevance classification failed content_id=%s: %s",
                item.id,
                exc,
            )

    content_repo.mark_ai_processed(
        item.id,
        summary=summary,
        topics=topics,
        commit=False,
    )

    if starters and not getattr(item, "starter_answers", None):
        from app.services.conversation_starters import get_starters_service

        try:
            get_starters_service(llm_client).generate_answers_and_persist(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[content_ai] starter-answer generation failed for %s: %s",
                item.id,
                exc,
            )

    return True


def _process_video_summary(
    db: Session,
    *,
    item: ContentItem,
    content_repo: ContentItemRepository,
    llm_client: LLMClient,
) -> bool:
    text = item.content_text or item.description or item.title
    result = llm_client.summarize_video(item.title, text)
    summary = normalize_video_summary_output(result.summary)
    topics = item.topics
    starters = result.conversation_starters
    item.tech_relevance = result.tech_relevance
    item.tech_relevance_confidence = result.tech_relevance_confidence
    item.tech_relevance_reason = result.tech_relevance_reason
    item.is_mixed_roundup = result.is_mixed_roundup

    classification_only = (
        getattr(item, "tech_relevance", None) == "none"
        or bool(getattr(item, "is_mixed_roundup", False))
    )
    summary_is_valid = is_video_summary_acceptable(summary)

    if summary_is_valid:
        content_repo.mark_ai_processed(
            item.id,
            summary=summary,
            topics=topics,
            commit=False,
        )
        if starters and not getattr(item, "starter_answers", None):
            from app.services.conversation_starters import get_starters_service

            if not item.conversation_starters:
                item.conversation_starters = starters
            try:
                get_starters_service(llm_client).generate_answers_and_persist(item)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[content_ai] video starter-answer generation failed for %s: %s",
                    item.id,
                    exc,
                )
        return True

    if classification_only:
        item.ai_processed = True
        item.summary = None
        sync_content_readiness(db, item)
        return True

    logger.warning("[content_ai] invalid video summary content_id=%s", item.id)
    return False


def _is_recent_article_for_maintenance(item: ContentItem, *, now: datetime | None = None) -> bool:
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return False
    now_utc = now or datetime.utcnow()
    return published_at >= now_utc - timedelta(days=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS)


def _article_retry_state(item: ContentItem, *, now: datetime | None = None) -> dict[str, object]:
    now_utc = now or datetime.utcnow()
    summary = (getattr(item, "summary", None) or "").strip()
    attempts = 0
    first_failed_at = None
    if summary.startswith(f"{_ARTICLE_RETRY_SENTINEL_PREFIX}:"):
        parts = summary.split(":", 3)
        if len(parts) >= 4:
            try:
                attempts = max(0, int(parts[2]))
            except (TypeError, ValueError):
                attempts = 0
            try:
                first_failed_at = datetime.fromisoformat(parts[3])
            except ValueError:
                first_failed_at = None

    if first_failed_at is None:
        return {"attempts": attempts, "first_failed_at": None, "eligible": True}

    retry_window = timedelta(hours=int(settings.ARTICLE_UNSKIMMABLE_RETRY_WINDOW_HOURS))
    within_window = first_failed_at + retry_window > now_utc
    max_attempts = int(settings.ARTICLE_UNSKIMMABLE_RETRY_MAX_ATTEMPTS)
    eligible = not within_window or attempts < max_attempts
    return {
        "attempts": attempts,
        "first_failed_at": first_failed_at,
        "eligible": eligible,
    }


def _record_article_retry_deferral(
    db: Session,
    item: ContentItem,
    *,
    now: datetime | None = None,
) -> int:
    now_utc = now or datetime.utcnow()
    state = _article_retry_state(item, now=now_utc)
    retry_window = timedelta(hours=int(settings.ARTICLE_UNSKIMMABLE_RETRY_WINDOW_HOURS))
    first_failed_at = state["first_failed_at"]
    attempts = int(state["attempts"])
    if first_failed_at is None or first_failed_at + retry_window <= now_utc:
        first_failed_at = now_utc
        attempts = 0
    attempts += 1
    item.summary = (
        f"{_ARTICLE_RETRY_SENTINEL_PREFIX}:v1:{attempts}:{first_failed_at.isoformat()}"
    )
    item.updated_at = now_utc
    return attempts


def _refresh_article_retry_inputs(
    article_hydrator: ArticleHydrationService,
    item: ContentItem,
) -> bool:
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
        item,
        "published_at",
        None,
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
