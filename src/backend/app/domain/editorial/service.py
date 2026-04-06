"""
Editorial service — orchestrates admin content operations.

All business rules for manual submission, deduplication, boosting,
and suppression live here.  The service is injected with repositories
and is therefore unit-testable without a database.
"""

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.article_hydration import ArticleHydrationService, finalize_article_image_verification
from app.core.curation import review_queue_target_status
from app.core.logging import get_logger
from app.ingestion.canonical import (
    canonical_key_for_article,
    canonical_key_for_youtube,
    extract_youtube_video_id,
)
from app.ingestion.service import build_video_content_item_from_entry
from app.ingestion.url_normalizer import normalize_url
from app.integrations.llm_client import LLMClient
from app.integrations.youtube_client import YouTubeClient
from app.models.content import ContentItem, ContentStatus, ContentType
from app.ranking.quality import compute_source_weight
from app.repositories.editorial_repo import EditorialRepository
from app.scheduler.tasks_content_events import run_content_event_dispatch_job
from app.services.content_readiness import (
    ContentReadinessStatus,
    describe_readiness_reason,
    evaluate_content_readiness,
    seed_content_readiness,
)
from app.video_surface_rules import has_explicit_shorts_url

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class SubmitResult:
    content_id: Optional[int]
    duplicate: bool
    status: str  # created | duplicate_boosted | duplicate_exists | error
    message: str
    content_type: Optional[str] = None


class EditorialApprovalBlockedError(RuntimeError):
    """Raised when a manual promote/approve action would still leave content unready."""

    def __init__(self, *, content_id: int, readiness_reason: str):
        self.content_id = content_id
        self.readiness_reason = readiness_reason
        self.detail = describe_readiness_reason(readiness_reason)
        super().__init__(self.detail)


class _NoopLLMClient:
    """Fallback client that disables optional video summarization."""

    @staticmethod
    def is_configured() -> bool:
        return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EditorialService:
    """Business logic for editorial / admin actions."""

    def __init__(self, db: Session, repo: Optional[EditorialRepository] = None):
        self.db = db
        self.repo = repo or EditorialRepository(db)
        self._article_hydrator: Optional[ArticleHydrationService] = None
        self._youtube_client: Optional[YouTubeClient] = None
        self._llm_client: Optional[LLMClient | _NoopLLMClient] = None

    # ------------------------------------------------------------------
    # Manual URL submission
    # ------------------------------------------------------------------

    def submit_url(
        self,
        url: str,
        importance_level: int = 0,
        actor: str = "admin",
    ) -> SubmitResult:
        """
        Submit a URL for ingestion.
        """
        normalized = normalize_url(url) or url
        youtube_video_id = extract_youtube_video_id(normalized)
        if youtube_video_id:
            return self._submit_youtube_url(
                source_url=normalized,
                video_id=youtube_video_id,
                importance_level=importance_level,
                actor=actor,
            )
        return self._submit_article_url(
            source_url=normalized,
            importance_level=importance_level,
            actor=actor,
        )

    def _submit_article_url(
        self,
        *,
        source_url: str,
        importance_level: int,
        actor: str,
    ) -> SubmitResult:
        """Submit a standard article URL using the existing stub flow."""
        existing = self.repo.get_by_source_url(source_url)

        if existing is None:
            ckey = canonical_key_for_article(canonical_url=None, source_url=source_url)
            if ckey:
                existing = self.repo.get_by_canonical_key(ckey)

        if existing is not None:
            return self._duplicate_result(
                existing=existing,
                importance_level=importance_level,
                actor=actor,
                fallback_content_type=ContentType.ARTICLE,
            )

        now = datetime.now(tz=None)
        stub = self._get_article_hydrator().build_article_stub(
            source_url=source_url,
            published_at=now,
            manual_added=True,
            added_by=actor,
            added_at=now,
            editorial_boost=importance_level,
            quality_score=0.5,
            discovered_via="manual",
        )
        return self._persist_new_submission(
            item=stub,
            submitted_url=source_url,
            importance_level=importance_level,
            actor=actor,
            created_message="Content stub created (id={content_id}). Pending enrichment.",
            duplicate_lookup=lambda: self._find_existing_article_item(source_url),
            fallback_content_type=ContentType.ARTICLE,
        )

    def _submit_youtube_url(
        self,
        *,
        source_url: str,
        video_id: str,
        importance_level: int,
        actor: str,
    ) -> SubmitResult:
        """Submit a YouTube URL as a real VIDEO or REEL item."""
        existing = self._find_existing_youtube_item(source_url=source_url, video_id=video_id)
        if existing is not None:
            return self._duplicate_result(
                existing=existing,
                importance_level=importance_level,
                actor=actor,
            )

        now = datetime.now(tz=None)
        youtube_client = self._get_youtube_client()
        entry = youtube_client.resolve_shared_url(source_url, acquisition_lane="curated")

        if entry is not None:
            item = build_video_content_item_from_entry(
                entry,
                youtube_client=youtube_client,
                llm_client=self._get_llm_client(),
                curation_status=review_queue_target_status(),
                discovered_via="manual",
                acquisition_lane="curated",
                manual_added=True,
                added_by=actor,
                added_at=now,
                editorial_boost=importance_level,
                skip_language_filter=True,
            )
            if item is None:
                logger.warning("Manual YouTube submit fell back to stub for %s", source_url)
                item = self._build_manual_youtube_stub(
                    source_url=source_url,
                    video_id=video_id,
                    actor=actor,
                    added_at=now,
                    editorial_boost=importance_level,
                )
        else:
            logger.warning("Manual YouTube submit could not resolve metadata for %s", source_url)
            item = self._build_manual_youtube_stub(
                source_url=source_url,
                video_id=video_id,
                actor=actor,
                added_at=now,
                editorial_boost=importance_level,
            )

        normalized_video_url = normalize_url(getattr(item, "video_url", None)) or normalize_url(
            getattr(item, "source_url", None)
        )
        if normalized_video_url:
            item.video_url = normalized_video_url
            item.source_url = normalized_video_url
            item.canonical_url = normalized_video_url
        else:
            item.source_url = source_url
            item.canonical_url = source_url

        item.canonical_key = canonical_key_for_youtube(
            video_id=video_id,
            source_url=item.source_url,
            video_url=item.video_url,
        )
        item.manual_added = True
        item.added_by = actor
        item.added_at = now
        item.editorial_boost = importance_level
        item.discovered_via = "manual"
        item.acquisition_lane = "curated"
        item.source = item.source or "YouTube"
        seed_content_readiness(item, now=now)

        content_type_value = self._content_type_value(item)
        created_label = {
            ContentType.REEL.value: "Manual reel queued",
            ContentType.VIDEO.value: "Manual video queued",
        }.get(content_type_value, "Manual YouTube content queued")
        return self._persist_new_submission(
            item=item,
            submitted_url=item.source_url,
            importance_level=importance_level,
            actor=actor,
            created_message=f"{created_label} (id={{content_id}}). Pending review.",
            duplicate_lookup=lambda: self._find_existing_youtube_item(
                source_url=item.source_url,
                video_id=video_id,
            ),
        )

    def _persist_new_submission(
        self,
        *,
        item: ContentItem,
        submitted_url: str,
        importance_level: int,
        actor: str,
        created_message: str,
        duplicate_lookup,
        fallback_content_type: ContentType | None = None,
    ) -> SubmitResult:
        self.db.add(item)
        try:
            self.db.commit()
            self.db.refresh(item)
        except IntegrityError:
            self.db.rollback()
            existing = duplicate_lookup()
            if existing is not None:
                return self._duplicate_result(
                    existing=existing,
                    importance_level=importance_level,
                    actor=actor,
                    fallback_content_type=fallback_content_type,
                )
            return SubmitResult(
                content_id=None,
                duplicate=True,
                status="duplicate_exists",
                message="Duplicate detected on insert (race condition).",
                content_type=self._content_type_value(item, fallback=fallback_content_type),
            )

        self.repo.log_add_action(
            content_id=item.id,
            actor=actor,
            url=submitted_url,
            importance_level=importance_level,
        )
        self.db.commit()
        return SubmitResult(
            content_id=item.id,
            duplicate=False,
            status="created",
            message=created_message.format(content_id=item.id),
            content_type=self._content_type_value(item, fallback=fallback_content_type),
        )

    def _duplicate_result(
        self,
        *,
        existing: ContentItem,
        importance_level: int,
        actor: str,
        fallback_content_type: ContentType | None = None,
    ) -> SubmitResult:
        if importance_level > 0 and (existing.editorial_boost or 0) < importance_level:
            self.repo.set_boost(existing.id, importance_level, actor)
            return SubmitResult(
                content_id=existing.id,
                duplicate=True,
                status="duplicate_boosted",
                message=f"Duplicate found (id={existing.id}). Boost updated to {importance_level}.",
                content_type=self._content_type_value(existing, fallback=fallback_content_type),
            )
        return SubmitResult(
            content_id=existing.id,
            duplicate=True,
            status="duplicate_exists",
            message=f"Duplicate found (id={existing.id}). No changes applied.",
            content_type=self._content_type_value(existing, fallback=fallback_content_type),
        )

    def _find_existing_article_item(self, source_url: str) -> Optional[ContentItem]:
        existing = self.repo.get_by_source_url(source_url)
        if existing is not None:
            return existing
        ckey = canonical_key_for_article(canonical_url=None, source_url=source_url)
        if ckey:
            return self.repo.get_by_canonical_key(ckey)
        return None

    def _find_existing_youtube_item(
        self,
        *,
        source_url: str,
        video_id: Optional[str],
    ) -> Optional[ContentItem]:
        candidates = [normalize_url(source_url) or source_url]
        if video_id:
            candidates.extend(
                [
                    normalize_url(f"https://www.youtube.com/watch?v={video_id}"),
                    normalize_url(f"https://www.youtube.com/shorts/{video_id}"),
                ]
            )

        seen_urls: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen_urls:
                continue
            seen_urls.add(candidate)
            existing = self.repo.get_by_source_url(candidate)
            if existing is not None:
                return existing

        ckey = canonical_key_for_youtube(
            video_id=video_id,
            source_url=source_url,
            video_url=source_url,
        )
        if ckey:
            return self.repo.get_by_canonical_key(ckey)
        return None

    def _build_manual_youtube_stub(
        self,
        *,
        source_url: str,
        video_id: Optional[str],
        actor: str,
        added_at: datetime,
        editorial_boost: int,
    ) -> ContentItem:
        content_type = (
            ContentType.REEL if has_explicit_shorts_url(source_url) else ContentType.VIDEO
        )
        title = (
            "[pending] YouTube Short"
            if content_type == ContentType.REEL
            else "[pending] YouTube Video"
        )
        stub = ContentItem(
            type=content_type,
            source="YouTube",
            source_url=source_url,
            canonical_url=source_url,
            canonical_key=canonical_key_for_youtube(
                video_id=video_id,
                source_url=source_url,
                video_url=source_url,
            ),
            published_at=added_at,
            title=title,
            description=None,
            content_text=None,
            summary=None,
            image_url=YouTubeClient.get_thumbnail_url(video_id) if video_id else None,
            video_url=source_url,
            topics=[],
            entities=[],
            dedupe_key=f"yt:{video_id}" if video_id else None,
            ai_processed=False,
            language="en",
            quality_score=compute_source_weight("YouTube"),
            recency_score=1.0,
            curation_status=review_queue_target_status(),
            discovered_via="manual",
            acquisition_lane="curated",
            manual_added=True,
            added_by=actor,
            added_at=added_at,
            editorial_boost=editorial_boost,
        )
        seed_content_readiness(stub, now=added_at)
        return stub

    # ------------------------------------------------------------------
    # Editorial approval
    # ------------------------------------------------------------------

    def promote_content(
        self,
        content_id: int,
        *,
        actor: str = "admin",
        dispatch_events: bool = True,
    ) -> Optional[ContentItem]:
        """Promote content after best-effort enrichment."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        self._ensure_ready_for_manual_promotion(item)
        promoted = self.repo.promote(content_id, actor=actor)
        if dispatch_events:
            self.dispatch_content_events_best_effort()
        return promoted

    def approve_content(
        self,
        content_id: int,
        *,
        actor: str = "admin",
        note: Optional[str] = None,
        dispatch_events: bool = True,
    ) -> Optional[ContentItem]:
        """Approve content after best-effort enrichment."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        self._ensure_ready_for_manual_promotion(item)
        approved = self.repo.approve(content_id, actor=actor, note=note)
        if dispatch_events:
            self.dispatch_content_events_best_effort()
        return approved

    def approve_and_publish(
        self,
        content_id: int,
        *,
        actor: str = "admin",
        boost_level: int = 3,
        note: Optional[str] = None,
        dispatch_events: bool = True,
    ) -> Optional[ContentItem]:
        """Approve content, enrich it, and publish it to the top of the feed."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        self._ensure_ready_for_manual_promotion(item)
        published = self.repo.approve_and_publish(
            content_id=content_id,
            actor=actor,
            boost_level=boost_level,
            note=note,
        )
        if dispatch_events:
            self.dispatch_content_events_best_effort()
        return published

    def _hydrate_for_approval(self, item: ContentItem) -> None:
        """Best-effort enrichment before a candidate becomes feed-visible."""
        hydrator = self._get_article_hydrator()
        if hydrator.needs_hydration(item):
            try:
                hydrator.hydrate_article_candidate(item)
            except Exception as exc:
                logger.warning(
                    "Editorial approval hydration failed for %s: %s",
                    getattr(item, "source_url", None) or getattr(item, "canonical_url", None),
                    exc,
                )

        if item.type == ContentType.ARTICLE:
            finalize_article_image_verification(item)

    def _ensure_ready_for_manual_promotion(self, item: ContentItem) -> None:
        """Prevent manual approval flows from promoting content that still cannot ship."""
        preview = SimpleNamespace(
            id=getattr(item, "id", None),
            type=item.type,
            curation_status=ContentStatus.PROMOTED,
            is_suppressed=False,
            promotion_reason=getattr(item, "promotion_reason", None),
            source_url=getattr(item, "source_url", None),
            canonical_url=getattr(item, "canonical_url", None),
            image_url=getattr(item, "image_url", None),
            article_image_status=getattr(item, "article_image_status", None),
            ai_processed=bool(getattr(item, "ai_processed", False)),
            summary=getattr(item, "summary", None),
            title=getattr(item, "title", None),
            video_url=getattr(item, "video_url", None),
        )
        decision = evaluate_content_readiness(preview)
        if decision.status != ContentReadinessStatus.READY:
            raise EditorialApprovalBlockedError(
                content_id=int(getattr(item, "id", 0) or 0),
                readiness_reason=decision.reason,
            )

    def _get_article_hydrator(self) -> ArticleHydrationService:
        """Lazily construct the shared hydrator used by editorial actions."""
        if self._article_hydrator is None:
            self._article_hydrator = ArticleHydrationService()
        return self._article_hydrator

    def _get_youtube_client(self) -> YouTubeClient:
        if self._youtube_client is None:
            self._youtube_client = YouTubeClient()
        return self._youtube_client

    def _get_llm_client(self) -> LLMClient | _NoopLLMClient:
        if self._llm_client is None:
            try:
                self._llm_client = LLMClient()
            except Exception as exc:
                logger.warning("Falling back to no-op LLM client for manual submit: %s", exc)
                self._llm_client = _NoopLLMClient()
        return self._llm_client

    def dispatch_content_events_best_effort(self) -> None:
        try:
            run_content_event_dispatch_job()
        except Exception as exc:
            logger.warning("Content event dispatch failed after editorial action: %s", exc)

    @staticmethod
    def _content_type_value(
        item: ContentItem,
        *,
        fallback: ContentType | None = None,
    ) -> Optional[str]:
        item_type = getattr(item, "type", None)
        if item_type is None:
            return fallback.value if fallback else None
        return item_type.value if hasattr(item_type, "value") else str(item_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    """Extract domain name from URL for source field."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Remove www. prefix
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return "unknown"
