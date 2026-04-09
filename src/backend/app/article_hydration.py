"""Shared best-effort hydration for thin article stubs.

This helper is used when an article is about to become feed-visible but
still lacks page-level metadata such as image, canonical URL, or extracted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape as html_unescape
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from app.article_image_selection import (
    ArticleImageCandidate,
    page_metadata_candidate_source,
    select_best_article_image,
)
from app.config.source_tiering import DomainTier, get_domain_tier
from app.core.config import get_settings
from app.core.logging import get_logger
from app.extraction.normalize import make_absolute_url
from app.ingestion.canonical import canonical_key_for_article
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.models.content import ContentItem, ContentType
from app.services.content_readiness import seed_content_readiness

logger = get_logger(__name__)
settings = get_settings()

ARTICLE_IMAGE_STATUS_PENDING = "PENDING"
ARTICLE_IMAGE_STATUS_VERIFIED = "VERIFIED"
ARTICLE_IMAGE_STATUS_MISSING = "MISSING"

_GENERIC_PATH_SEGMENTS = {
    "article",
    "articles",
    "blog",
    "feature",
    "features",
    "news",
    "post",
    "posts",
    "story",
    "stories",
}
_DATE_SEGMENT_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")
_FILE_EXT_RE = re.compile(r"\.[a-z0-9]{1,6}$", re.IGNORECASE)
_INLINE_IMAGE_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_BLOCKED_DIRECT_ARTICLE_EXTENSIONS = {
    ".7z",
    ".avi",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".m4v",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".tar",
    ".tgz",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


@dataclass(frozen=True)
class ArticleImageLLMExtractionResult:
    """Outcome for the article-image LLM fallback, including diagnostics."""

    image_url: Optional[str]
    reason: str
    raw_candidate_url: Optional[str] = None
    error: Optional[str] = None


def _humanize_slug_segment(segment: str) -> str:
    words = [part for part in re.split(r"[-_]+", segment) if part]
    if not words:
        return ""

    preserved = {
        "ai": "AI",
        "api": "API",
        "cpu": "CPU",
        "gpu": "GPU",
        "ios": "iOS",
        "macos": "macOS",
        "nvidia": "NVIDIA",
        "openai": "OpenAI",
        "uk": "UK",
        "us": "US",
    }
    rendered = []
    for word in words:
        lowered = word.lower()
        if lowered in preserved:
            rendered.append(preserved[lowered])
        elif word.isupper() and len(word) <= 5:
            rendered.append(word)
        else:
            rendered.append(word.capitalize())
    return " ".join(rendered).strip()


def derive_article_title_from_url(url: Optional[str]) -> Optional[str]:
    """Build a readable fallback title from a URL slug or domain."""
    parsed = urlparse((url or "").strip())
    segments = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]

    for segment in reversed(segments):
        cleaned = _FILE_EXT_RE.sub("", segment)
        lowered = cleaned.lower()
        if not cleaned or lowered in _GENERIC_PATH_SEGMENTS or _DATE_SEGMENT_RE.fullmatch(lowered):
            continue
        title = _humanize_slug_segment(cleaned)
        if len(title.split()) >= 2:
            return title

    host = (parsed.netloc or "").strip().lower()
    if host:
        host = host.removeprefix("www.")
        return f"Article from {host}"
    return None


def build_pending_article_title(url: Optional[str]) -> str:
    """Create a pending placeholder title without leaking the raw URL into the UI."""
    fallback = derive_article_title_from_url(url) or "Article pending enrichment"
    return f"[pending] {fallback}"


def display_article_title(title: Optional[str], source_url: Optional[str]) -> str:
    """Return a user-facing title, replacing pending placeholders with URL-derived fallbacks."""
    cleaned = (title or "").strip()
    if cleaned and not looks_like_pending_title(cleaned):
        return cleaned
    return derive_article_title_from_url(source_url) or cleaned or "Untitled"


def bounded_article_summary_text(article_text: Optional[str]) -> Optional[str]:
    """Return summary input clipped to configured bounds, or None when too short."""
    cleaned = (article_text or "").strip()
    if not cleaned:
        return None

    min_words = max(1, int(settings.ARTICLE_SUMMARY_MIN_WORDS))
    max_words = max(min_words, int(settings.ARTICLE_SUMMARY_MAX_WORDS))
    words = cleaned.split()
    if len(words) < min_words:
        return None
    if len(words) <= max_words:
        return cleaned
    return " ".join(words[:max_words])


def seed_article_image_verification(item: Any) -> None:
    """Mark article rows as awaiting post-ingest image verification."""
    if getattr(item, "type", None) != ContentType.ARTICLE:
        return
    item.article_image_status = ARTICLE_IMAGE_STATUS_PENDING
    item.article_image_checked_at = None


def finalize_article_image_verification(item: Any, *, now: Optional[datetime] = None) -> None:
    """Persist the outcome of article image verification."""
    if getattr(item, "type", None) != ContentType.ARTICLE:
        return
    item.article_image_status = (
        ARTICLE_IMAGE_STATUS_VERIFIED
        if ArticleHydrationService.normalize_article_image(getattr(item, "image_url", None))
        else ARTICLE_IMAGE_STATUS_MISSING
    )
    item.article_image_checked_at = now or datetime.utcnow()


def normalize_article_summary_output(summary_text: Optional[str]) -> Optional[str]:
    """Normalize generated article summaries while enforcing the max output bound."""
    cleaned = " ".join((summary_text or "").split()).strip()
    if not cleaned:
        return None

    max_words = max(1, int(settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS))
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned

    return " ".join(words[:max_words]).rstrip(" ,;:-")


@dataclass
class PreparedArticle:
    """Shared article payload prepared from an RSS entry or page extraction."""

    source_url: str
    canonical_url: str
    title: str
    description: Optional[str]
    content_text: Optional[str]
    image_url: Optional[str]
    published_at: datetime
    source: str
    topics: list
    entities: list
    extraction: Optional[Any] = None


class ArticleHydrationService:
    """Best-effort extraction + summary hydration for article items."""

    def __init__(self, *, llm_client: Optional[Any] = None):
        self._llm_client: Optional[Any] = llm_client

    def build_article_stub(
        self,
        *,
        source_url: str,
        title: Optional[str] = None,
        canonical_url: Optional[str] = None,
        published_at: Optional[datetime] = None,
        description: Optional[str] = None,
        content_text: Optional[str] = None,
        image_url: Optional[str] = None,
        summary: Optional[str] = None,
        topics: Optional[list] = None,
        entities: Optional[list] = None,
        curation_status=None,
        discovered_via: Optional[str] = None,
        signal_hits: int = 0,
        quality_score: float = 0.5,
        manual_added: bool = False,
        added_by: Optional[str] = None,
        added_at: Optional[datetime] = None,
        editorial_boost: int = 0,
        candidate_first_seen_at: Optional[datetime] = None,
        candidate_signal_source: Optional[str] = None,
        candidate_raw_title: Optional[str] = None,
    ) -> ContentItem:
        """Build a normalized article stub so all curation paths start the same way."""
        normalized_source_url = (source_url or "").strip()
        normalized_canonical = (canonical_url or normalized_source_url).strip()
        normalized_title = (title or "").strip()[:1000] or build_pending_article_title(
            normalized_canonical or normalized_source_url
        )
        normalized_description = (description or "").strip()[:500] or None
        normalized_content_text = (content_text or "").strip()[:8000] or None
        normalized_image_url = self.normalize_article_image(image_url)
        normalized_summary = normalize_article_summary_output(summary)

        topic_seed = (
            normalized_summary
            or normalized_content_text
            or normalized_description
            or normalized_title
        )
        resolved_topics = (
            topics if topics is not None else extract_topics(normalized_title, topic_seed)
        )
        resolved_entities = (
            entities
            if entities is not None
            else extract_entities(normalized_title, normalized_summary or topic_seed)
        )

        stub = ContentItem(
            type=ContentType.ARTICLE,
            source=extract_source(normalized_canonical or normalized_source_url),
            source_url=normalized_source_url,
            canonical_url=normalized_canonical or None,
            canonical_key=canonical_key_for_article(
                canonical_url=normalized_canonical or None,
                source_url=normalized_source_url,
            ),
            published_at=published_at or datetime.utcnow(),
            title=normalized_title,
            description=normalized_description,
            content_text=normalized_content_text,
            summary=normalized_summary,
            image_url=normalized_image_url,
            video_url=None,
            topics=resolved_topics or [],
            entities=resolved_entities or [],
            ai_processed=bool(normalized_summary),
            language="en",
            quality_score=quality_score,
            recency_score=1.0,
            trend_score=0.0,
            global_score=0.0,
            signal_hits=signal_hits,
            manual_added=manual_added,
            added_by=added_by,
            added_at=added_at,
            editorial_boost=editorial_boost,
            candidate_first_seen_at=candidate_first_seen_at,
            candidate_signal_source=candidate_signal_source,
            candidate_raw_title=(candidate_raw_title or normalized_title)[:1000]
            if (candidate_raw_title or normalized_title)
            else None,
            discovered_via=discovered_via,
        )
        if curation_status is not None:
            stub.curation_status = curation_status
        seed_article_image_verification(stub)
        seed_content_readiness(stub)
        return stub

    def prepare_rss_article(
        self,
        *,
        source_url: str,
        title: str,
        description: Optional[str],
        image_url: Optional[str],
        published_at: Optional[datetime],
        include_text: bool,
    ) -> Optional[PreparedArticle]:
        """Prepare shared article fields from an RSS entry for any ingest path."""
        normalized_source_url = (source_url or "").strip()
        if not self.is_direct_article_url_allowed(normalized_source_url):
            return None
        normalized_description = (description or "").strip()
        prepared = PreparedArticle(
            source_url=normalized_source_url,
            canonical_url=normalized_source_url,
            title=(title or "").strip() or build_pending_article_title(normalized_source_url),
            description=normalized_description[:500] or None,
            content_text=normalized_description[:8000] or None,
            image_url=select_best_article_image(
                [ArticleImageCandidate(url=image_url, source="rss")]
            ),
            published_at=published_at or datetime.utcnow(),
            source=extract_source(normalized_source_url),
            topics=[],
            entities=[],
            extraction=None,
        )

        if include_text:
            extraction = self.extract_article_from_rss(
                source_url=normalized_source_url,
                title=prepared.title,
                description=prepared.description,
                image_url=prepared.image_url,
                published_at=prepared.published_at,
            )
            prepared.extraction = extraction
            if extraction is not None:
                extracted_title = (getattr(extraction, "title", None) or "").strip()
                extracted_canonical = (getattr(extraction, "canonical_url", None) or "").strip()
                extracted_image = (getattr(extraction, "image_url", None) or "").strip()
                extracted_text = (
                    getattr(extraction, "main_text", None)
                    or getattr(extraction, "excerpt_fallback", None)
                    or ""
                ).strip()
                extracted_published_at = getattr(extraction, "published_at", None)

                if extracted_title:
                    prepared.title = extracted_title
                if extracted_canonical:
                    prepared.canonical_url = extracted_canonical
                if extracted_published_at is not None:
                    prepared.published_at = extracted_published_at
                if extracted_text:
                    prepared.content_text = extracted_text[:8000]
                prepared.image_url = select_best_article_image(
                    [
                        ArticleImageCandidate(url=prepared.image_url, source="rss"),
                        ArticleImageCandidate(url=extracted_image, source="extraction"),
                    ]
                )
        else:
            metadata = self.fetch_article_page_metadata(normalized_source_url)
            if metadata is not None:
                extracted_title = (getattr(metadata, "title", None) or "").strip()
                extracted_canonical = (getattr(metadata, "canonical_url", None) or "").strip()
                extracted_image = (getattr(metadata, "image_url", None) or "").strip()
                if extracted_title:
                    prepared.title = extracted_title
                if extracted_canonical:
                    prepared.canonical_url = extracted_canonical
                prepared.image_url = select_best_article_image(
                    [
                        ArticleImageCandidate(url=prepared.image_url, source="rss"),
                        ArticleImageCandidate(
                            url=extracted_image,
                            source=page_metadata_candidate_source(
                                getattr(metadata, "image_source", None)
                            ),
                        ),
                    ]
                )

        prepared.source = extract_source(prepared.canonical_url or prepared.source_url)
        topic_seed = (prepared.content_text or prepared.description or prepared.title or "").strip()
        prepared.topics = extract_topics(prepared.title or "", topic_seed) or []
        prepared.entities = extract_entities(prepared.title or "", topic_seed) or []
        return prepared

    def apply_prepared_article(self, item: ContentItem, prepared: PreparedArticle) -> None:
        """Apply a prepared article payload onto a ContentItem."""
        if prepared.title:
            item.title = prepared.title
        if prepared.canonical_url:
            item.canonical_url = prepared.canonical_url
        if prepared.published_at is not None:
            item.published_at = prepared.published_at
        if prepared.description and not (item.description or "").strip():
            item.description = prepared.description
        if prepared.content_text:
            item.content_text = prepared.content_text[:8000]
        selected_image = select_best_article_image(
            [
                ArticleImageCandidate(url=item.image_url, source="existing"),
                ArticleImageCandidate(url=prepared.image_url, source="prepared"),
            ]
        )
        if selected_image and selected_image != (item.image_url or "").strip():
            item.image_url = selected_image
        if prepared.source:
            item.source = prepared.source
        if prepared.topics and not item.topics:
            item.topics = prepared.topics
        if prepared.entities and not item.entities:
            item.entities = prepared.entities

    def refresh_article_identity(self, item: ContentItem) -> None:
        """Normalize source and display title after extraction or metadata repair."""
        source_url = (item.canonical_url or item.source_url or "").strip()
        if not source_url:
            return

        item.source = extract_source(source_url)
        if looks_like_pending_title(item.title) or not (item.title or "").strip():
            item.title = display_article_title(item.title, source_url)

    def populate_article_summary(self, item: ContentItem) -> bool:
        """Generate article summary fields when the item still needs AI enrichment."""
        if item.type != ContentType.ARTICLE:
            return False
        if item.ai_processed and (item.summary or "").strip():
            return False

        article_text = (item.content_text or item.description or "").strip()
        if not article_text and not looks_like_pending_title(item.title):
            article_text = (item.title or "").strip()

        summary_input = bounded_article_summary_text(article_text)
        if not summary_input:
            return False

        summary_result = self.summarize_article(item, summary_input)
        summary = normalize_article_summary_output(getattr(summary_result, "summary", None))
        if not summary or len(summary) <= 50:
            return False

        item.summary = summary
        item.ai_processed = True

        starters = getattr(summary_result, "conversation_starters", None)
        if starters:
            item.conversation_starters = starters

        tags = getattr(summary_result, "tags", None)
        if tags:
            item.topics = tags
        return True

    def refresh_article_annotations(self, item: ContentItem) -> None:
        """Refresh article source, topics, and entities using the best available text."""
        self.refresh_article_identity(item)

        topic_seed = (item.summary or item.content_text or item.description or "").strip()
        refreshed_topics = extract_topics(item.title or "", topic_seed)
        refreshed_entities = extract_entities(item.title or "", item.summary or topic_seed)

        if refreshed_topics and (not item.topics or not item.ai_processed):
            item.topics = refreshed_topics
        if refreshed_entities or not item.entities:
            item.entities = refreshed_entities

    def needs_hydration(self, item: ContentItem) -> bool:
        """Return True when an article still looks like a thin stub."""
        from app.extraction.metadata import is_probably_generic_image_url

        if item.type != ContentType.ARTICLE:
            return False

        title = (item.title or "").strip()
        image_url = (item.image_url or "").strip()
        return any(
            (
                looks_like_pending_title(title),
                not (item.canonical_url or "").strip(),
                not (item.content_text or "").strip(),
                not (item.summary or "").strip(),
                not bool(item.ai_processed),
                not image_url,
                bool(image_url) and is_probably_generic_image_url(image_url),
            )
        )

    def hydrate_article_candidate(self, item: ContentItem) -> None:
        """Apply extraction, image recovery, and summary generation to an article item."""
        extraction = self.run_article_extraction(item)

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
            selected_image = select_best_article_image(
                [
                    ArticleImageCandidate(url=item.image_url, source="existing"),
                    ArticleImageCandidate(url=extracted_image, source="extraction"),
                ]
            )
            if selected_image and selected_image != (item.image_url or "").strip():
                item.image_url = selected_image

        self.refresh_article_identity(item)
        self.populate_article_summary(item)
        self.refresh_article_annotations(item)

    def run_article_extraction(self, item: ContentItem):
        """Execute the shared extraction pipeline using current item fields as fallback."""
        return self.extract_article_from_rss(
            source_url=(item.canonical_url or item.source_url or "").strip(),
            title=None if looks_like_pending_title(item.title) else item.title,
            description=item.description,
            image_url=item.image_url,
            published_at=item.published_at,
        )

    def extract_article_from_rss(
        self,
        *,
        source_url: str,
        title: Optional[str],
        description: Optional[str],
        image_url: Optional[str],
        published_at: Optional[datetime],
    ):
        """Execute the shared extraction pipeline with RSS-derived fallback fields."""
        from app.extraction.pipeline import RSSEntryData, run_extraction

        article_url = (source_url or "").strip()
        if not article_url or not self.is_direct_article_url_allowed(article_url):
            return None

        return run_extraction(
            article_url,
            rss_entry=RSSEntryData(
                title=title,
                description=description,
                image_url=image_url,
                published_date=published_at,
            ),
        )

    def refresh_existing_article_metadata(
        self,
        item: ContentItem,
        *,
        source_url: Optional[str],
        rss_image_url: Optional[str] = None,
        force_reconcile_image: bool = False,
    ) -> bool:
        """Refresh missing or suspicious image/canonical metadata on an existing article."""
        from app.extraction.metadata import is_probably_generic_image_url
        from app.extraction.normalize import is_suspicious_image_url

        current_image_url = (item.image_url or "").strip()
        needs_image = (
            force_reconcile_image
            or not current_image_url
            or is_probably_generic_image_url(current_image_url)
            or is_suspicious_image_url(current_image_url)
        )
        needs_canonical = not (item.canonical_url or "").strip()
        if not needs_image and not needs_canonical:
            return False

        metadata = None
        if source_url and self.is_direct_article_url_allowed(source_url):
            metadata = self.fetch_article_page_metadata(source_url)
            if metadata is not None and needs_canonical and metadata.canonical_url:
                item.canonical_url = metadata.canonical_url

        image_candidates = [
            ArticleImageCandidate(url=rss_image_url, source="rss"),
            ArticleImageCandidate(
                url=(getattr(metadata, "image_url", None) or "").strip()
                if metadata is not None
                else None,
                source=page_metadata_candidate_source(
                    getattr(metadata, "image_source", None) if metadata is not None else None
                ),
            ),
        ]
        if not force_reconcile_image:
            image_candidates.insert(0, ArticleImageCandidate(url=item.image_url, source="existing"))

        refreshed_image = select_best_article_image(image_candidates)
        if not refreshed_image and needs_image:
            llm_result = self.extract_article_image_with_llm(
                article_url=source_url,
                title=display_article_title(item.title, source_url),
            )
            llm_image_url = getattr(llm_result, "image_url", llm_result)
            refreshed_image = select_best_article_image(
                [
                    ArticleImageCandidate(url=item.image_url, source="existing"),
                    ArticleImageCandidate(url=rss_image_url, source="rss"),
                    ArticleImageCandidate(
                        url=llm_image_url if llm_result else None,
                        source="llm_extract",
                    ),
                ],
                allow_generic_fallback=True,
            )
        if not refreshed_image:
            refreshed_image = select_best_article_image(
                image_candidates, allow_generic_fallback=True
            )

        changed = False
        if refreshed_image and refreshed_image != current_image_url:
            item.image_url = refreshed_image
            changed = True
        if needs_canonical and (item.canonical_url or "").strip():
            changed = True
        if changed:
            item.updated_at = datetime.utcnow()
        return changed

    @staticmethod
    def needs_article_metadata_repair(item: ContentItem, *, include_generic: bool) -> bool:
        """Return True when an article should be revisited for metadata repair."""
        from app.extraction.metadata import is_probably_generic_image_url
        from app.extraction.normalize import is_suspicious_image_url

        has_missing_image = not (item.image_url or "").strip()
        has_missing_canonical = not (item.canonical_url or "").strip()
        has_generic_image = include_generic and is_probably_generic_image_url(item.image_url)
        has_suspicious_image = is_suspicious_image_url(item.image_url)
        return (
            has_missing_image or has_missing_canonical or has_generic_image or has_suspicious_image
        )

    @staticmethod
    def fetch_article_page_metadata(article_url: str):
        """Fetch and extract best-effort page metadata for an article URL."""
        if not article_url or not ArticleHydrationService.is_direct_article_url_allowed(
            article_url
        ):
            return None

        try:
            from app.extraction.fetcher import fetch_url
            from app.extraction.metadata import extract_metadata

            fetch = fetch_url(article_url)
            if fetch.error or not fetch.html:
                return None
            return extract_metadata(fetch.html, fetch.url or article_url)
        except Exception as exc:
            logger.debug("Article metadata fetch failed for %s: %s", article_url, exc)
            return None

    def extract_article_image_with_llm(
        self,
        *,
        article_url: Optional[str],
        title: Optional[str],
    ) -> Optional[str]:
        """Use the LLM as a last-resort parser for article-owned image URLs."""
        return self.extract_article_image_with_llm_diagnostics(
            article_url=article_url,
            title=title,
        ).image_url

    def extract_article_image_with_llm_diagnostics(
        self,
        *,
        article_url: Optional[str],
        title: Optional[str],
    ) -> ArticleImageLLMExtractionResult:
        """Run LLM image recovery and preserve why a recovery did or did not happen."""
        if not settings.ARTICLE_IMAGE_LLM_FALLBACK_ENABLED:
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="llm_fallback_disabled",
            )

        normalized_article_url = (article_url or "").strip()
        if not normalized_article_url or not self.is_direct_article_url_allowed(
            normalized_article_url
        ):
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="article_url_not_allowed",
            )

        llm_client = self._get_llm_client()
        if llm_client is None:
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="llm_not_configured",
            )

        try:
            from app.extraction.fetcher import fetch_url
            from app.extraction.metadata import extract_metadata

            fetch = fetch_url(normalized_article_url)
            if fetch.error or not fetch.html:
                fetch_reason = "article_fetch_failed"
                if (fetch.error or "").startswith("BOT_PROTECTED:"):
                    fetch_reason = "article_bot_protected"
                return ArticleImageLLMExtractionResult(
                    image_url=None,
                    reason=fetch_reason,
                    error=fetch.error or f"HTTP {fetch.status_code or 0}",
                )

            document = self._build_llm_image_extraction_document(
                fetch.html,
                fetch.url or normalized_article_url,
            )
            if not document:
                return ArticleImageLLMExtractionResult(
                    image_url=None,
                    reason="document_empty",
                )

            fresh_metadata = extract_metadata(
                fetch.html,
                fetch.url or normalized_article_url,
            )
            fresh_metadata_image = select_best_article_image(
                [
                    ArticleImageCandidate(
                        url=getattr(fresh_metadata, "image_url", None),
                        source=page_metadata_candidate_source(
                            getattr(fresh_metadata, "image_source", None)
                        ),
                    ),
                ]
            )
            if fresh_metadata_image:
                verified_metadata_image = self._confirm_fetched_image_url(fresh_metadata_image)
                if verified_metadata_image:
                    return ArticleImageLLMExtractionResult(
                        image_url=verified_metadata_image,
                        reason="fresh_page_metadata",
                        raw_candidate_url=getattr(fresh_metadata, "image_url", None),
                    )

            document_candidate_image = self._extract_article_image_from_document_candidates(
                html=fetch.html,
                article_url=fetch.url or normalized_article_url,
            )
            if document_candidate_image:
                return ArticleImageLLMExtractionResult(
                    image_url=document_candidate_image,
                    reason="document_candidate",
                    raw_candidate_url=document_candidate_image,
                )

            try:
                candidate = llm_client.extract_article_image_url(
                    article_url=fetch.url or normalized_article_url,
                    title=(title or "").strip()
                    or display_article_title(None, fetch.url or normalized_article_url),
                    document=document,
                )
            except Exception as exc:
                logger.warning("LLM article image extraction failed for %s: %s", article_url, exc)
                return ArticleImageLLMExtractionResult(
                    image_url=None,
                    reason="llm_error",
                    error=str(exc),
                )

            validated = self._validate_llm_extracted_image_url_with_diagnostics(
                raw_candidate_url=candidate,
                html=fetch.html,
                article_url=fetch.url or normalized_article_url,
            )
            if validated.image_url:
                logger.info(
                    "Recovered article image via LLM fallback for %s -> %s",
                    normalized_article_url,
                    validated.image_url,
                )
            return validated
        except Exception as exc:
            logger.warning("LLM article image extraction failed for %s: %s", article_url, exc)
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="unexpected_error",
                error=str(exc),
            )

    @staticmethod
    def _build_llm_image_extraction_document(html: str, source_url: str) -> Optional[str]:
        """Compress page content down to image-relevant snippets for the LLM."""
        cleaned_html = (html or "").strip()
        if not cleaned_html:
            return None

        lines = [f"SOURCE_URL: {source_url}"]
        try:
            soup = BeautifulSoup(cleaned_html, "html.parser")
        except Exception:
            soup = None

        if soup is not None:
            head = soup.find("head") or soup
            for tag in head.find_all("meta"):
                prop = (tag.get("property") or tag.get("name") or "").strip().lower()
                content = (tag.get("content") or "").strip()
                if (
                    prop
                    and content
                    and any(keyword in prop for keyword in ("image", "title", "description"))
                ):
                    lines.append(f"META: {str(tag)[:400]}")

            for link in head.find_all("link", rel=True):
                rel_value = link.get("rel")
                if isinstance(rel_value, list):
                    rel = " ".join(str(part) for part in rel_value)
                else:
                    rel = str(rel_value or "")
                href = (link.get("href") or "").strip()
                if href and "image" in rel.lower():
                    lines.append(f"LINK: {str(link)[:400]}")

            for tag in soup.find_all(["img", "source", "picture"], limit=30):
                rendered = str(tag).strip()
                if rendered:
                    lines.append(f"MEDIA: {rendered[:500]}")

            for script in soup.find_all(
                "script",
                attrs={"type": re.compile("ld\\+json", re.IGNORECASE)},
            ):
                payload = script.get_text(" ", strip=True)
                if payload and "image" in payload.lower():
                    lines.append(f"JSON_LD: {payload[:1000]}")

        seen_urls: set[str] = set()
        for match in _INLINE_IMAGE_URL_RE.finditer(cleaned_html):
            url = match.group(0).strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)
            lines.append(f"RAW_URL: {url}")
            if len(seen_urls) >= 30:
                break

        document = "\n".join(lines).strip()
        if not document:
            return None
        return document[: int(settings.ARTICLE_IMAGE_LLM_MAX_INPUT_CHARS)]

    @staticmethod
    def _validate_llm_extracted_image_url(
        *,
        raw_candidate_url: Optional[str],
        html: str,
        article_url: str,
    ) -> Optional[str]:
        return ArticleHydrationService._validate_llm_extracted_image_url_with_diagnostics(
            raw_candidate_url=raw_candidate_url,
            html=html,
            article_url=article_url,
        ).image_url

    @staticmethod
    def _confirm_fetched_image_url(candidate_url: Optional[str]) -> Optional[str]:
        """Return the candidate only when it resolves to an actual image response."""
        from app.extraction.fetcher import fetch_url

        candidate = (candidate_url or "").strip()
        if not candidate:
            return None

        fetch = fetch_url(candidate)
        if fetch.error or fetch.status_code >= 400:
            return None

        content_type = (fetch.content_type or "").lower()
        if "image/" not in content_type:
            return None

        return candidate

    @staticmethod
    def _extract_article_image_from_document_candidates(
        *,
        html: str,
        article_url: str,
    ) -> Optional[str]:
        """Try direct document image candidates before falling back to the LLM."""
        from app.extraction.metadata import _best_srcset_candidate, _looks_like_editorial_image

        cleaned_html = (html or "").strip()
        if not cleaned_html:
            return None

        try:
            soup = BeautifulSoup(cleaned_html, "html.parser")
        except Exception:
            return None

        seen_roots: set[int] = set()
        for root in (soup.find("article"), soup.find("main"), soup.body, soup):
            if root is None or id(root) in seen_roots:
                continue
            seen_roots.add(id(root))
            for tag in root.find_all(["picture", "img", "source"], limit=30):
                for candidate in ArticleHydrationService._document_tag_candidate_urls(
                    tag,
                    best_srcset_candidate=_best_srcset_candidate,
                ):
                    if not candidate or not _looks_like_editorial_image(tag, candidate):
                        continue
                    validated = (
                        ArticleHydrationService._validate_llm_extracted_image_url_with_diagnostics(
                            raw_candidate_url=candidate,
                            html=cleaned_html,
                            article_url=article_url,
                        )
                    )
                    if validated.image_url:
                        return validated.image_url

        return None

    @staticmethod
    def _document_tag_candidate_urls(tag, *, best_srcset_candidate) -> list[str]:
        """Collect raw candidate URLs from a media tag in priority order."""
        nodes = []
        if getattr(tag, "name", None) == "picture":
            nodes.extend(tag.find_all("source"))
            img = tag.find("img")
            if img is not None:
                nodes.append(img)
        else:
            nodes.append(tag)

        candidates: list[str] = []
        for node in nodes:
            for attr in (
                "data-src",
                "data-lazy-src",
                "data-original",
                "data-image",
                "data-url",
                "data-srcset",
                "srcset",
                "src",
            ):
                value = node.get(attr)
                if not value or not str(value).strip():
                    continue
                if attr.endswith("srcset"):
                    candidate = best_srcset_candidate(str(value))
                    if candidate:
                        candidates.append(candidate)
                    continue
                candidates.append(str(value).strip())
        return candidates

    @staticmethod
    def _validate_llm_extracted_image_url_with_diagnostics(
        *,
        raw_candidate_url: Optional[str],
        html: str,
        article_url: str,
    ) -> ArticleImageLLMExtractionResult:
        """Verify the LLM returned a real article-owned image URL from the page."""
        from app.extraction.fetcher import fetch_url
        from app.extraction.metadata import is_probably_generic_image_url
        from app.extraction.normalize import (
            is_suspicious_image_url,
            validate_image_url,
        )

        candidate = (raw_candidate_url or "").strip()
        if not candidate:
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="llm_returned_none",
            )

        if not ArticleHydrationService._html_contains_candidate_reference(html, candidate):
            return ArticleImageLLMExtractionResult(
                image_url=None,
                reason="candidate_not_in_document",
                raw_candidate_url=candidate,
            )

        saw_invalid_url = False
        saw_rejected_candidate = False
        saw_fetch_failure = False
        saw_not_image = False

        for absolute in ArticleHydrationService._candidate_absolute_image_urls(
            candidate,
            article_url,
        ):
            validated = validate_image_url(absolute)
            if not validated:
                saw_invalid_url = True
                continue
            if is_probably_generic_image_url(validated) or is_suspicious_image_url(validated):
                saw_rejected_candidate = True
                continue

            fetch = fetch_url(validated)
            if fetch.error or fetch.status_code >= 400:
                saw_fetch_failure = True
                continue

            content_type = (fetch.content_type or "").lower()
            if "image/" not in content_type:
                saw_not_image = True
                continue

            return ArticleImageLLMExtractionResult(
                image_url=validated,
                reason="recovered",
                raw_candidate_url=candidate,
            )

        if saw_fetch_failure:
            reason = "candidate_fetch_failed"
        elif saw_not_image:
            reason = "candidate_not_image"
        elif saw_rejected_candidate:
            reason = "candidate_rejected"
        elif saw_invalid_url:
            reason = "candidate_invalid_url"
        else:
            reason = "candidate_validation_failed"

        return ArticleImageLLMExtractionResult(
            image_url=None,
            reason=reason,
            raw_candidate_url=candidate,
        )

    @staticmethod
    def _html_contains_candidate_reference(html: str, candidate_url: str) -> bool:
        """Check that the model returned a URL string that actually appears in the page."""
        haystacks = {
            (html or ""),
            html_unescape(html or ""),
        }
        needles = {
            (candidate_url or "").strip(),
            html_unescape((candidate_url or "").strip()),
            (candidate_url or "").strip().replace("&", "&amp;"),
        }

        for haystack in haystacks:
            lowered_haystack = haystack.lower()
            for needle in needles:
                if needle and needle.lower() in lowered_haystack:
                    return True
        return False

    @staticmethod
    def _candidate_absolute_image_urls(candidate_url: str, article_url: str) -> list[str]:
        """Return plausible absolute URLs for an LLM-returned image reference.

        Some publishers embed article-owned assets as root-level paths without a
        leading slash (for example ``news/2026/...jpg``). ``urljoin`` treats
        those as page-relative and can produce a broken nested path. Keep the
        RFC-compliant resolution first, but also try same-origin root
        resolution as a fallback for these CMS-style asset paths.
        """

        candidate = (candidate_url or "").strip()
        if not candidate:
            return []

        parsed_candidate = urlparse(candidate)
        if parsed_candidate.scheme and parsed_candidate.netloc:
            return [candidate]
        if candidate.startswith("//"):
            return [f"https:{candidate}"]

        candidates: list[str] = []
        resolved = make_absolute_url(candidate, article_url)
        if resolved:
            candidates.append(resolved)

        if not candidate.startswith(("/", "./", "../", "?", "#")):
            parsed_article = urlparse(article_url)
            if parsed_article.scheme and parsed_article.netloc:
                root_resolved = (
                    f"{parsed_article.scheme}://{parsed_article.netloc}/{candidate.lstrip('/')}"
                )
                if root_resolved not in candidates:
                    candidates.append(root_resolved)

        return candidates

    @classmethod
    def normalize_article_image(cls, candidate_image_url: Optional[str]) -> Optional[str]:
        """Return a validated editorial image URL or None."""
        return select_best_article_image(
            [ArticleImageCandidate(url=candidate_image_url, source="direct")]
        )

    @staticmethod
    def is_direct_article_url_allowed(article_url: Optional[str]) -> bool:
        """Return True when a URL is not on the hard-blocked non-editorial list."""
        normalized = (article_url or "").strip()
        if not normalized or get_domain_tier(normalized) == DomainTier.BLOCKED:
            return False

        parsed = urlparse(normalized)
        path = (unquote(parsed.path or "")).lower()
        for ext in _BLOCKED_DIRECT_ARTICLE_EXTENSIONS:
            if path.endswith(ext):
                return False

        return True

    def summarize_article(self, item: ContentItem, article_text: str):
        """Return a best-effort summary result, or None when the LLM is unavailable."""
        llm_client = self._get_llm_client()
        if llm_client is None:
            return None
        return llm_client.summarize_article(
            display_article_title(item.title, item.canonical_url or item.source_url),
            article_text,
        )

    def _get_llm_client(self):
        """Lazily construct the shared LLM client used during hydration."""
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
    def should_replace_article_image(
        existing_image_url: Optional[str],
        candidate_image_url: Optional[str],
        *,
        candidate_source: str = "direct",
    ) -> bool:
        """Prefer real editorial images and avoid filling blanks with generic placeholders."""
        selected_image = select_best_article_image(
            [
                ArticleImageCandidate(url=existing_image_url, source="existing"),
                ArticleImageCandidate(url=candidate_image_url, source=candidate_source),
            ]
        )
        return bool(selected_image and selected_image != (existing_image_url or "").strip())


def looks_like_pending_title(title: Optional[str]) -> bool:
    """Return True when a title still looks like an unhydrated stub."""
    return (title or "").strip().lower().startswith("[pending]")
