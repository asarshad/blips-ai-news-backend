"""Main text extraction with cascading fallbacks.

Strategy:
  1. trafilatura (primary) — best at boilerplate removal
  2. readability-lxml (fallback) — good at finding the main content block
  3. RSS description/summary (last resort) — stored as excerpt_fallback

Each extractor returns plain text. The caller decides which to use based
on quality heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger
from app.extraction.normalize import clean_text, is_good_text

logger = get_logger(__name__)


@dataclass
class TextResult:
    """Result from text extraction attempt."""

    text: Optional[str] = None
    extractor: str = "none"  # trafilatura | readability | rss_only
    word_count: int = 0
    is_good: bool = False


def extract_with_trafilatura(html: str, url: Optional[str] = None) -> TextResult:
    """Extract main text using trafilatura.

    trafilatura is the best general-purpose extractor for news articles.
    It removes boilerplate (nav, footer, ads) and returns clean text.
    """
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            deduplicate=True,
            favor_precision=True,
        )

        if text:
            text = clean_text(text)
            wc = len(text.split())
            return TextResult(
                text=text,
                extractor="trafilatura",
                word_count=wc,
                is_good=is_good_text(text),
            )
    except ImportError:
        logger.error("[text_extract] trafilatura not installed")
    except Exception as exc:
        logger.warning(f"[text_extract] trafilatura failed for {url}: {exc}")

    return TextResult(extractor="trafilatura")


def extract_with_readability(html: str, url: Optional[str] = None) -> TextResult:
    """Extract main text using readability-lxml (Mozilla Readability port).

    readability-lxml finds the main content block and strips everything else.
    We then extract plain text from the cleaned HTML.
    """
    try:
        from readability import Document

        doc = Document(html, url=url)
        cleaned_html = doc.summary()

        if cleaned_html:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(cleaned_html, "lxml")
            text = soup.get_text(separator="\n", strip=True)
            text = clean_text(text)
            wc = len(text.split())
            return TextResult(
                text=text,
                extractor="readability",
                word_count=wc,
                is_good=is_good_text(text),
            )
    except ImportError:
        logger.error("[text_extract] readability-lxml not installed")
        return TextResult(extractor="readability_unavailable")
    except Exception as exc:
        logger.warning(f"[text_extract] readability failed for {url}: {exc}")

    return TextResult(extractor="readability")


def extract_text(
    html: str,
    url: Optional[str] = None,
    rss_description: Optional[str] = None,
) -> TextResult:
    """Extract main text with cascading fallbacks.

    1. Try trafilatura (best quality)
    2. If poor quality, fallback to readability-lxml
    3. If still poor, use RSS description as excerpt

    Args:
        html: Full HTML page content.
        url: The page URL (used for relative URL resolution).
        rss_description: RSS feed description for last-resort fallback.

    Returns:
        TextResult with the best available text.
    """
    if not html:
        # No HTML to parse — fall back to RSS
        if rss_description:
            cleaned = clean_text(rss_description)
            wc = len(cleaned.split()) if cleaned else 0
            return TextResult(
                text=cleaned if cleaned else None,
                extractor="rss_only",
                word_count=wc,
                is_good=is_good_text(cleaned),
            )
        return TextResult()

    # 1. Try trafilatura
    result = extract_with_trafilatura(html, url=url)
    if result.is_good:
        return result

    # 2. Fallback: readability-lxml
    rb_result = extract_with_readability(html, url=url)
    if rb_result.is_good:
        return rb_result

    # If trafilatura returned *something* (just low quality), prefer it over readability
    if result.text and result.word_count > 0:
        if not rb_result.text or result.word_count >= rb_result.word_count:
            return result

    # If readability returned something, use it
    if rb_result.text and rb_result.word_count > 0:
        return rb_result

    # 3. Last resort: RSS description
    if rss_description:
        cleaned = clean_text(rss_description)
        wc = len(cleaned.split()) if cleaned else 0
        return TextResult(
            text=cleaned if cleaned else None,
            extractor="rss_only",
            word_count=wc,
            is_good=is_good_text(cleaned),
        )

    return TextResult()
