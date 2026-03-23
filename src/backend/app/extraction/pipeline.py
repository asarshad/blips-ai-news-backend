"""Extraction pipeline: orchestrates fetching, metadata, and text extraction.

Entry point: ``run_extraction(source_url, rss_entry=...)`` returns an
``ExtractionResult`` with all fields populated (or marked as FAILED).

This function never raises — all errors are captured in the result.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────


class ExtractionStatus(str, enum.Enum):
    OK = "OK"
    FALLBACK_USED = "FALLBACK_USED"
    FAILED = "FAILED"


class ImageStatus(str, enum.Enum):
    OK = "OK"
    MISSING = "MISSING"
    INVALID = "INVALID"


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ExtractionResult:
    """Full extraction result for a single article."""

    source_url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    published_at: Optional[datetime] = None
    image_url: Optional[str] = None
    main_text: Optional[str] = None
    excerpt_fallback: Optional[str] = None

    extraction_status: ExtractionStatus = ExtractionStatus.FAILED
    image_status: ImageStatus = ImageStatus.MISSING
    image_source: str = "none"  # og | twitter | rss | none
    text_quality_score: float = 0.0
    word_count: int = 0
    extractor_used: str = "none"  # trafilatura | readability | rss_only

    fetch_error: Optional[str] = None
    fetch_elapsed_ms: float = 0.0


# ── Optional RSS entry data ──────────────────────────────────────────────────


@dataclass
class RSSEntryData:
    """Data from the RSS feed entry, used as fallback and for enrichment."""

    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    published_date: Optional[datetime] = None


# ── Pipeline ──────────────────────────────────────────────────────────────────


def run_extraction(
    source_url: str,
    *,
    rss_entry: Optional[RSSEntryData] = None,
    skip_fetch: bool = False,
) -> ExtractionResult:
    """Run the full extraction pipeline for an article URL.

    Steps:
    1. Fetch HTML (unless skip_fetch=True)
    2. Extract metadata (canonical URL, title, image)
    3. Extract main text (trafilatura → readability → RSS fallback)
    4. Merge RSS entry data as fallback
    5. Validate and return ExtractionResult

    This function NEVER raises. All errors are captured in the result.
    """
    result = ExtractionResult(source_url=source_url)
    rss = rss_entry or RSSEntryData()

    html = ""
    fetched_url = source_url

    # ── Step 1: Fetch ─────────────────────────────────────────────────
    if not skip_fetch:
        try:
            from app.extraction.fetcher import fetch_url

            fetch = fetch_url(source_url)
            result.fetch_elapsed_ms = fetch.elapsed_ms
            result.fetch_error = fetch.error

            if fetch.error or not fetch.html:
                logger.warning(f"[extraction] Fetch failed for {source_url}: {fetch.error}")
                # Fall through to RSS-only extraction
            else:
                html = fetch.html
                fetched_url = fetch.url or source_url
        except Exception as exc:
            result.fetch_error = str(exc)
            logger.error(f"[extraction] Unexpected fetch error for {source_url}: {exc}")

    # ── Step 2: Metadata extraction ───────────────────────────────────
    page_title = None
    page_image = None
    page_image_source = "none"

    if html:
        try:
            from app.extraction.metadata import extract_metadata

            meta = extract_metadata(html, fetched_url)
            result.canonical_url = meta.canonical_url or fetched_url
            page_title = meta.title
            page_image = meta.image_url
            page_image_source = meta.image_source
            page_image_confidence = meta.image_confidence
            page_image_suspicious = meta.image_suspicious

            if meta.published_at_str:
                result.published_at = _try_parse_date(meta.published_at_str)
        except Exception as exc:
            logger.warning(f"[extraction] Metadata extraction failed for {source_url}: {exc}")

    # Canonical URL fallback
    if not result.canonical_url:
        result.canonical_url = fetched_url

    # ── Step 3: Title (page → RSS) ───────────────────────────────────
    result.title = page_title or rss.title

    # ── Step 4: Image (page → RSS) ───────────────────────────────────
    from app.extraction.normalize import validate_image_url

    if page_image:
        result.image_url = page_image
        result.image_source = page_image_source
        result.image_confidence = page_image_confidence
        result.image_suspicious = page_image_suspicious
        result.image_status = ImageStatus.OK
    elif rss.image_url:
        validated = validate_image_url(rss.image_url)
        if validated:
            result.image_url = validated
            result.image_source = "rss"
            result.image_status = ImageStatus.OK
        else:
            result.image_url = None
            result.image_status = ImageStatus.INVALID
    else:
        result.image_url = None
        result.image_status = ImageStatus.MISSING

    # ── Step 5: Published date (page → RSS) ──────────────────────────
    if not result.published_at and rss.published_date:
        result.published_at = rss.published_date

    # ── Step 6: Text extraction ──────────────────────────────────────
    try:
        from app.extraction.normalize import compute_text_quality_score
        from app.extraction.text_extract import extract_text

        text_result = extract_text(
            html=html,
            url=source_url,
            rss_description=rss.description,
        )

        result.extractor_used = text_result.extractor
        result.word_count = text_result.word_count

        if text_result.is_good and text_result.text:
            result.main_text = text_result.text
            result.text_quality_score = compute_text_quality_score(text_result.text)
            if text_result.extractor == "trafilatura":
                result.extraction_status = ExtractionStatus.OK
            else:
                result.extraction_status = ExtractionStatus.FALLBACK_USED
        elif text_result.text:
            # Have text but quality is low — store as excerpt fallback
            if text_result.extractor == "rss_only":
                result.excerpt_fallback = text_result.text
                result.extraction_status = ExtractionStatus.FALLBACK_USED
            else:
                # Partial extraction — use text but mark as fallback
                result.main_text = text_result.text
                result.text_quality_score = compute_text_quality_score(text_result.text)
                result.extraction_status = ExtractionStatus.FALLBACK_USED
        else:
            # No text at all — try RSS description as excerpt
            if rss.description:
                from app.extraction.normalize import clean_text

                excerpt = clean_text(rss.description)
                if excerpt:
                    result.excerpt_fallback = excerpt
                    result.extractor_used = "rss_only"
                    result.word_count = len(excerpt.split())
            result.extraction_status = ExtractionStatus.FAILED

    except Exception as exc:
        logger.error(f"[extraction] Text extraction failed for {source_url}: {exc}")
        result.extraction_status = ExtractionStatus.FAILED
        # Still try to capture RSS description
        if rss.description:
            try:
                from app.extraction.normalize import clean_text

                result.excerpt_fallback = clean_text(rss.description)
                result.extractor_used = "rss_only"
            except Exception:
                pass

    return result


def _try_parse_date(date_str: str) -> Optional[datetime]:
    """Best-effort date parsing from various meta tag formats.

    Always returns a timezone-aware datetime (UTC) or None.
    """
    from dateutil import parser as dateutil_parser

    try:
        dt = dateutil_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    # Try common ISO formats manually
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    return None
