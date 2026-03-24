"""
Editorial service — orchestrates admin content operations.

All business rules for manual submission, deduplication, boosting,
and suppression live here.  The service is injected with repositories
and is therefore unit-testable without a database.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.article_hydration import ArticleHydrationService
from app.core.logging import get_logger
from app.ingestion.canonical import canonical_key_for_article
from app.ingestion.url_normalizer import normalize_url
from app.models.content import ContentItem
from app.repositories.editorial_repo import EditorialRepository
from app.scheduler.tasks_content_events import run_content_event_dispatch_job

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
        self._article_hydrator: Optional[ArticleHydrationService] = None

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
        now = datetime.now(tz=None)
        stub = self._get_article_hydrator().build_article_stub(
            source_url=normalized,
            published_at=now,
            manual_added=True,
            added_by=actor,
            added_at=now,
            editorial_boost=importance_level,
            quality_score=0.5,
            discovered_via="manual",
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
        dispatch_events: bool = True,
    ) -> Optional[ContentItem]:
        """Promote content after best-effort enrichment."""
        item = self.repo.get_content_by_id(content_id)
        if item is None:
            return None

        self._hydrate_for_approval(item)
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
        if not hydrator.needs_hydration(item):
            return

        try:
            hydrator.hydrate_article_candidate(item)
        except Exception as exc:
            logger.warning(
                "Editorial approval hydration failed for %s: %s",
                getattr(item, "source_url", None) or getattr(item, "canonical_url", None),
                exc,
            )

    def _get_article_hydrator(self) -> ArticleHydrationService:
        """Lazily construct the shared hydrator used by editorial actions."""
        if self._article_hydrator is None:
            self._article_hydrator = ArticleHydrationService()
        return self._article_hydrator

    def dispatch_content_events_best_effort(self) -> None:
        try:
            run_content_event_dispatch_job()
        except Exception as exc:
            logger.warning("Content event dispatch failed after editorial action: %s", exc)


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
