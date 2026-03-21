"""
Editorial service — orchestrates admin content operations.

All business rules for manual submission, deduplication, boosting,
and suppression live here.  The service is injected with repositories
and is therefore unit-testable without a database.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.canonical import canonical_key_for_article
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.ingestion.url_normalizer import normalize_url
from app.models.content import ContentItem, ContentType
from app.repositories.editorial_repo import EditorialRepository

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


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EditorialService:
    """Business logic for editorial / admin actions."""

    def __init__(self, db: Session, repo: Optional[EditorialRepository] = None):
        self.db = db
        self.repo = repo or EditorialRepository(db)
        self._llm_client: Optional[Any] = None

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

        1. Canonicalize URL.
        2. Check for duplicates using canonical_key and source_url.
        3. If duplicate exists, optionally apply boost.
        4. If new, create a stub content item and mark it for
           enrichment by the existing ingestion pipeline.
        """
        normalized = normalize_url(url) or url

        # ── Duplicate detection (same logic as ingestion pipeline) ────
        existing = self.repo.get_by_source_url(normalized)

        if existing is None:
            # Also try canonical_key lookup
            ckey = canonical_key_for_article(canonical_url=None, source_url=normalized)
            if ckey:
                existing = self.repo.get_by_canonical_key(ckey)

        if existing is not None:
            if importance_level > 0 and existing.editorial_boost < importance_level:
                self.repo.set_boost(existing.id, importance_level, actor)
                return SubmitResult(
                    content_id=existing.id,
                    duplicate=True,
                    status="duplicate_boosted",
                    message=f"Duplicate found (id={existing.id}). Boost updated to {importance_level}.",
                )
            return SubmitResult(
                content_id=existing.id,
                duplicate=True,
                status="duplicate_exists",
                message=f"Duplicate found (id={existing.id}). No changes applied.",
            )

        # ── Create stub content item ──────────────────────────────────
        ckey = canonical_key_for_article(canonical_url=None, source_url=normalized)

        stub = ContentItem(
            type=ContentType.ARTICLE,  # default; enrichment may reclassify
            source=_extract_domain(normalized),
            source_url=normalized,
            canonical_url=normalized,
            canonical_key=ckey,
            published_at=datetime.now(tz=None),
            title=f"[pending] {normalized}",
            ai_processed=False,
            manual_added=True,
            added_by=actor,
            added_at=datetime.now(tz=None),
            editorial_boost=importance_level,
            quality_score=0.5,
            recency_score=1.0,
        )

        self.db.add(stub)
        try:
            self.db.commit()
            self.db.refresh(stub)
        except IntegrityError:
            self.db.rollback()
            # Race condition — someone else inserted it between checks
            existing = self.repo.get_by_source_url(normalized)
            return SubmitResult(
                content_id=existing.id if existing else None,
                duplicate=True,
                status="duplicate_exists",
                message="Duplicate detected on insert (race condition).",
            )

        # Audit log
        self.repo.log_add_action(
            content_id=stub.id,
            actor=actor,
            url=normalized,
            importance_level=importance_level,
        )
        self.db.commit()

        return SubmitResult(
            content_id=stub.id,
            duplicate=False,
            status="created",
            message=f"Content stub created (id={stub.id}). Pending enrichment.",
        )

    # ------------------------------------------------------------------
    # Editorial approval
    # ------------------------------------------------------------------

    def promote_content(
        self,
        content_id: int,
        *,
        actor: str = "admin",
    ) -> Optional[ContentItem]:
        """Promote content after best-effort enrichment."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        return self.repo.promote(content_id, actor=actor)

    def approve_content(
        self,
        content_id: int,
        *,
        actor: str = "admin",
        note: Optional[str] = None,
    ) -> Optional[ContentItem]:
        """Approve content after best-effort enrichment."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        return self.repo.approve(content_id, actor=actor, note=note)

    def approve_and_publish(
        self,
        content_id: int,
        *,
        actor: str = "admin",
        boost_level: int = 3,
        note: Optional[str] = None,
    ) -> Optional[ContentItem]:
        """Approve content, enrich it, and publish it to the top of the feed."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
        return self.repo.approve_and_publish(
            content_id=content_id,
            actor=actor,
            boost_level=boost_level,
            note=note,
        )

    def _hydrate_for_approval(self, item: ContentItem) -> None:
        """Best-effort enrichment before a candidate becomes feed-visible."""
        if item.type != ContentType.ARTICLE:
            return

        if not self._needs_article_hydration(item):
            return

        try:
            self._hydrate_article_candidate(item)
        except Exception as exc:
            logger.warning(
                "Editorial approval hydration failed for %s: %s",
                getattr(item, "source_url", None) or getattr(item, "canonical_url", None),
                exc,
            )

    def _hydrate_article_candidate(self, item: ContentItem) -> None:
        """Apply extraction, image recovery, and summary generation to an article candidate."""
        extraction = self._run_article_extraction(item)

        if extraction is not None:
            extracted_title = (getattr(extraction, "title", None) or "").strip()
            if extracted_title:
                item.title = extracted_title

            extracted_canonical = (getattr(extraction, "canonical_url", None) or "").strip()
            if extracted_canonical:
                item.canonical_url = extracted_canonical

            extracted_published_at = getattr(extraction, "published_at", None)
            if extracted_published_at is not None:
                item.published_at = extracted_published_at

            extracted_text = getattr(extraction, "main_text", None) or getattr(
                extraction, "excerpt_fallback", None
            )
            if extracted_text:
                item.content_text = extracted_text[:8000]

            extracted_image = (getattr(extraction, "image_url", None) or "").strip()
            if self._should_replace_article_image(item.image_url, extracted_image):
                item.image_url = extracted_image

        source_url = (item.canonical_url or item.source_url or "").strip()
        if source_url:
            item.source = extract_source(source_url)

        if not (item.ai_processed and (item.summary or "").strip()):
            article_text = (item.content_text or item.description or "").strip()
            if not article_text and not _looks_like_pending_title(item.title):
                article_text = (item.title or "").strip()
            if article_text:
                summary_result = self._summarize_article(item, article_text)
                summary = (getattr(summary_result, "summary", None) or "").strip()
                if summary and len(summary) > 50:
                    item.summary = summary
                    item.ai_processed = True
                    starters = getattr(summary_result, "conversation_starters", None)
                    if starters:
                        item.conversation_starters = starters

        topic_seed = (item.summary or item.content_text or item.description or "").strip()
        refreshed_topics = extract_topics(item.title or "", topic_seed)
        refreshed_entities = extract_entities(item.title or "", item.summary or topic_seed)
        if refreshed_topics or not item.topics:
            item.topics = refreshed_topics
        if refreshed_entities or not item.entities:
            item.entities = refreshed_entities

    def _run_article_extraction(self, item: ContentItem):
        """Execute the shared extraction pipeline using current item fields as fallback."""
        from app.extraction.pipeline import RSSEntryData, run_extraction

        article_url = (item.canonical_url or item.source_url or "").strip()
        if not article_url:
            return None

        return run_extraction(
            article_url,
            rss_entry=RSSEntryData(
                title=None if _looks_like_pending_title(item.title) else item.title,
                description=item.description,
                image_url=item.image_url,
                published_date=item.published_at,
            ),
        )

    def _summarize_article(self, item: ContentItem, article_text: str):
        """Return a best-effort article summary result, or None when unavailable."""
        llm_client = self._get_llm_client()
        if llm_client is None:
            return None
        return llm_client.summarize_article(
            item.title or item.source_url or "Untitled", article_text
        )

    def _get_llm_client(self):
        """Lazily construct the shared LLM client used during approval hydration."""
        if self._llm_client is False:
            return None
        if self._llm_client is not None:
            return self._llm_client

        from app.integrations.llm_client import LLMClient

        client = LLMClient()
        if not client.is_configured():
            self._llm_client = False
            return None

        self._llm_client = client
        return client

    @staticmethod
    def _needs_article_hydration(item: ContentItem) -> bool:
        """Return True when an article still looks like an unhydrated candidate."""
        from app.extraction.metadata import is_probably_generic_image_url

        title = (item.title or "").strip()
        image_url = (item.image_url or "").strip()
        return any(
            (
                _looks_like_pending_title(title),
                not (item.canonical_url or "").strip(),
                not (item.content_text or "").strip(),
                not (item.summary or "").strip(),
                not bool(item.ai_processed),
                not image_url,
                bool(image_url) and is_probably_generic_image_url(image_url),
            )
        )

    @staticmethod
    def _should_replace_article_image(
        existing_image_url: Optional[str], candidate_image_url: Optional[str]
    ) -> bool:
        """Prefer real editorial images and avoid filling blanks with generic placeholders."""
        from app.extraction.metadata import is_probably_generic_image_url

        candidate = (candidate_image_url or "").strip()
        if not candidate:
            return False

        existing = (existing_image_url or "").strip()
        candidate_is_generic = is_probably_generic_image_url(candidate)
        if not existing:
            return not candidate_is_generic
        if existing == candidate:
            return False
        if is_probably_generic_image_url(existing) and not candidate_is_generic:
            return True
        return False


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


def _looks_like_pending_title(title: Optional[str]) -> bool:
    """Return True when a title still looks like an unhydrated editorial stub."""
    return (title or "").strip().lower().startswith("[pending]")
