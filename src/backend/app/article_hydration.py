"""Shared best-effort hydration for thin article stubs.

This helper is used when an article is about to become feed-visible but
still lacks page-level metadata such as image, canonical URL, or extracted text.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
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


class ArticleHydrationService:
    """Best-effort extraction + summary hydration for article items."""

    def __init__(self):
        self._llm_client: Optional[Any] = None

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

        source_url = (item.canonical_url or item.source_url or "").strip()
        if source_url:
            item.source = extract_source(source_url)
            if looks_like_pending_title(item.title):
                item.title = display_article_title(item.title, source_url)

        if not (item.ai_processed and (item.summary or "").strip()):
            article_text = (item.content_text or item.description or "").strip()
            if not article_text and not looks_like_pending_title(item.title):
                article_text = (item.title or "").strip()
            summary_input = bounded_article_summary_text(article_text)
            if summary_input:
                summary_result = self.summarize_article(item, summary_input)
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

    def run_article_extraction(self, item: ContentItem):
        """Execute the shared extraction pipeline using current item fields as fallback."""
        from app.extraction.pipeline import RSSEntryData, run_extraction

        article_url = (item.canonical_url or item.source_url or "").strip()
        if not article_url:
            return None

        return run_extraction(
            article_url,
            rss_entry=RSSEntryData(
                title=None if looks_like_pending_title(item.title) else item.title,
                description=item.description,
                image_url=item.image_url,
                published_date=item.published_at,
            ),
        )

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
