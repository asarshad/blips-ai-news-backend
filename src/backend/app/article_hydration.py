"""Shared best-effort hydration for thin article stubs.

This helper is used when an article is about to become feed-visible but
still lacks page-level metadata such as image, canonical URL, or extracted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingestion.canonical import canonical_key_for_article
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.models.content import ContentItem, ContentType

logger = get_logger(__name__)
settings = get_settings()

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


def normalize_article_summary_output(summary_text: Optional[str]) -> Optional[str]:
    """Normalize generated article summaries while enforcing the max output bound."""
    cleaned = " ".join((summary_text or "").split()).strip()
    if not cleaned:
        return None

    max_words = max(1, int(settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS))
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned

    trimmed = " ".join(words[:max_words]).rstrip(" ,;:-")
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "..."
    return trimmed


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
    ) -> PreparedArticle:
        """Prepare shared article fields from an RSS entry for any ingest path."""
        normalized_source_url = (source_url or "").strip()
        normalized_description = (description or "").strip()
        prepared = PreparedArticle(
            source_url=normalized_source_url,
            canonical_url=normalized_source_url,
            title=(title or "").strip() or build_pending_article_title(normalized_source_url),
            description=normalized_description[:500] or None,
            content_text=normalized_description[:8000] or None,
            image_url=self.normalize_article_image(image_url),
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
                if self.should_replace_article_image(prepared.image_url, extracted_image):
                    prepared.image_url = extracted_image
        elif prepared.image_url is None:
            metadata = self.fetch_article_page_metadata(normalized_source_url)
            if metadata is not None:
                extracted_title = (getattr(metadata, "title", None) or "").strip()
                extracted_canonical = (getattr(metadata, "canonical_url", None) or "").strip()
                extracted_image = (getattr(metadata, "image_url", None) or "").strip()
                if extracted_title:
                    prepared.title = extracted_title
                if extracted_canonical:
                    prepared.canonical_url = extracted_canonical
                if self.should_replace_article_image(prepared.image_url, extracted_image):
                    prepared.image_url = extracted_image

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
        if self.should_replace_article_image(item.image_url, prepared.image_url):
            item.image_url = prepared.image_url
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
            if self.should_replace_article_image(item.image_url, extracted_image):
                item.image_url = extracted_image

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
        if not article_url:
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
    ) -> bool:
        """Refresh missing or suspicious image/canonical metadata on an existing article."""
        from app.extraction.metadata import is_probably_generic_image_url
        from app.extraction.normalize import validate_image_url

        current_image_url = (item.image_url or "").strip()
        needs_image = not current_image_url or is_probably_generic_image_url(current_image_url)
        needs_canonical = not (item.canonical_url or "").strip()
        if not needs_image and not needs_canonical:
            return False

        refreshed_image = None
        if rss_image_url:
            refreshed_image = self.normalize_article_image(validate_image_url(rss_image_url))

        if source_url and (
            needs_canonical
            or not refreshed_image
            or (current_image_url and is_probably_generic_image_url(current_image_url))
        ):
            metadata = self.fetch_article_page_metadata(source_url)
            if metadata is not None:
                metadata_image = (getattr(metadata, "image_url", None) or "").strip()
                if self.should_replace_article_image(
                    refreshed_image or current_image_url, metadata_image
                ):
                    refreshed_image = metadata_image
                if needs_canonical and metadata.canonical_url:
                    item.canonical_url = metadata.canonical_url

        changed = False
        if self.should_replace_article_image(item.image_url, refreshed_image):
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

        has_missing_image = not (item.image_url or "").strip()
        has_missing_canonical = not (item.canonical_url or "").strip()
        has_generic_image = include_generic and is_probably_generic_image_url(item.image_url)
        return has_missing_image or has_missing_canonical or has_generic_image

    @staticmethod
    def fetch_article_page_metadata(article_url: str):
        """Fetch and extract best-effort page metadata for an article URL."""
        if not article_url:
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

    @classmethod
    def normalize_article_image(cls, candidate_image_url: Optional[str]) -> Optional[str]:
        """Return a validated editorial image URL or None."""
        from app.extraction.normalize import validate_image_url

        candidate = validate_image_url(candidate_image_url)
        if cls.should_replace_article_image(None, candidate):
            return candidate
        return None

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


def looks_like_pending_title(title: Optional[str]) -> bool:
    """Return True when a title still looks like an unhydrated stub."""
    return (title or "").strip().lower().startswith("[pending]")
