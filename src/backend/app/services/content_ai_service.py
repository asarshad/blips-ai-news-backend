"""Event-driven AI summarization helpers for promoted content."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from app.services.ai_retry_state import (
    article_retry_state,
    record_article_retry_deferral,
    record_video_summary_failure,
)
from app.services.content_readiness import seed_content_readiness, sync_content_readiness
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.tiered_feed_service import invalidate_tiered_feed_cache
from app.services.video_relevance_service import classify_video_blips_relevance

logger = get_logger(__name__)

CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE = "content.ai_summary.requested"


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
    if (
        getattr(item, "type", None) == ContentType.ARTICLE
        and not article_retry_state(item)["eligible"]
    ):
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

    if item.type == ContentType.ARTICLE:
        retry_state = article_retry_state(item)
        if not retry_state["eligible"]:
            seed_content_readiness(item)
            logger.info(
                "[content_ai] skipping article outside unskimmable retry window "
                "content_id=%s attempts=%s",
                item.id,
                retry_state["attempts"],
            )
            return {
                "content_id": int(item.id),
                "changed": False,
                "skipped": True,
                "reason": "article_retry_window_exhausted",
                "content_type": item.type.value,
                "ai_processed": bool(item.ai_processed),
                "readiness_status": (
                    getattr(item, "readiness_status", None) or ""
                ).strip()
                or None,
                "readiness_reason": (
                    getattr(item, "readiness_reason", None) or ""
                ).strip()
                or None,
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
    previous_summary = item.summary
    item.ai_processed = False
    item.summary = None

    summary_input = bounded_article_summary_text(text)
    if not summary_input:
        if _is_recent_article_for_maintenance(item):
            item.summary = previous_summary
            attempt = record_article_retry_deferral(item)
            seed_content_readiness(item)
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
        allow_summary_rescue=_allow_summary_rescue_for_item(item),
    )
    article_hydrator.refresh_article_annotations(item)

    summary = item.summary
    topics = item.topics
    starters = item.conversation_starters

    if not bool(summary and len(summary.strip()) > 50):
        logger.warning("[content_ai] invalid article summary content_id=%s", item.id)
        return False

    tech_relevance = None
    tech_relevance_confidence = None
    tech_relevance_reason = None
    if settings.ARTICLE_TECH_CLASSIFIER_ENABLED:
        try:
            tech = llm_client.classify_blips_tech_relevance(
                title=item.title or "",
                summary=summary,
                source=item.source or "",
                url=item.source_url or None,
            )
            tech_relevance = tech.is_blips_tech_relevant
            tech_relevance_confidence = tech.confidence
            tech_relevance_reason = tech.reason
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
        tech_relevance=tech_relevance,
        tech_relevance_confidence=tech_relevance_confidence,
        tech_relevance_reason=tech_relevance_reason,
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
    relevance = classify_video_blips_relevance(
        llm_client,
        title=item.title or "",
        summary=text or "",
        source=item.source or "",
        url=item.source_url or None,
    )
    if relevance is not None and not relevance.is_relevant:
        item.tech_relevance = "none"
        item.tech_relevance_confidence = relevance.confidence
        item.tech_relevance_reason = relevance.reason
        item.is_mixed_roundup = False
        item.ai_processed = True
        item.summary = None
        sync_content_readiness(db, item)
        return True

    try:
        result = llm_client.summarize_video(
            item.title,
            text,
            allow_rescue=_allow_summary_rescue_for_item(item),
        )
    except ValueError as exc:
        return _record_video_summary_failure(
            item,
            reason=str(exc) or "empty_summary_exception",
        )

    summary = normalize_video_summary_output(result.summary)
    topics = item.topics
    starters = result.conversation_starters
    item.tech_relevance = result.tech_relevance
    item.tech_relevance_confidence = result.tech_relevance_confidence
    item.tech_relevance_reason = result.tech_relevance_reason
    item.is_mixed_roundup = result.is_mixed_roundup

    classification_only = getattr(item, "tech_relevance", None) == "none" or bool(
        getattr(item, "is_mixed_roundup", False)
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

    return _record_video_summary_failure(
        item,
        reason="empty_or_short_summary",
    )


def _is_recent_article_for_maintenance(item: ContentItem, *, now: datetime | None = None) -> bool:
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return False
    now_utc = _as_naive_utc(now or datetime.utcnow())
    return _as_naive_utc(published_at) >= now_utc - timedelta(
        days=settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS
    )


def _allow_summary_rescue_for_item(item: ContentItem, *, now: datetime | None = None) -> bool:
    """Allow full-model rescue only for recent promoted, unsuppressed content."""
    if getattr(item, "curation_status", None) != ContentStatus.PROMOTED:
        return False
    if bool(getattr(item, "is_suppressed", False)):
        return False
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return False

    now_utc = _as_naive_utc(now or datetime.utcnow())
    lookback_days = (
        settings.ARTICLE_MAINTENANCE_LOOKBACK_DAYS
        if getattr(item, "type", None) == ContentType.ARTICLE
        else 7
    )
    return _as_naive_utc(published_at) >= now_utc - timedelta(days=lookback_days)


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize mixed DB/API datetimes before comparing recency windows."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _article_retry_state(item: ContentItem, *, now: datetime | None = None) -> dict[str, object]:
    return article_retry_state(item, now=now)


def _record_article_retry_deferral(
    db: Session,
    item: ContentItem,
    *,
    now: datetime | None = None,
) -> int:
    attempt = record_article_retry_deferral(item, now=now)
    seed_content_readiness(item, now=now)
    return attempt


def _record_video_summary_failure(
    item: ContentItem,
    *,
    reason: str,
    now: datetime | None = None,
) -> bool:
    state = record_video_summary_failure(item, reason=reason, now=now)
    seed_content_readiness(item, now=now)
    logger.warning(
        "[content_ai] video summary failure content_id=%s attempt=%s terminal=%s reason=%s",
        item.id,
        state["attempts"],
        state["terminal"],
        reason,
    )
    return True


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
