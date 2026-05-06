"""Article-only editorial quality exclusions.

These rules catch recurring non-news/help/tool pages that can look
mechanically ready after summarization but should not be delivered as Blips
articles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, not_, or_

from app.models.content import ContentItem


@dataclass(frozen=True)
class ArticleQualityBlock:
    reason: str
    detail: str


_PUZZLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btoday'?s\s+(?:nyt\s+)?(?:wordle|connections|strands|mini\s+crossword|crossword)\b",
        r"\b(?:wordle|connections|strands|mini\s+crossword|crossword)\s+(?:hints?|answers?|help)\b",
        r"\b(?:hints?|answers?|help)\s+(?:for\s+)?(?:nyt\s+)?(?:wordle|connections|strands|mini\s+crossword|crossword)\b",
    )
)

_PUZZLE_URL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/todays-(?:nyt-)?(?:wordle|connections|strands|mini-crossword|crossword)",
        r"/(?:nyt-)?(?:wordle|connections|strands|mini-crossword|crossword)-.*(?:hints?|answers?|help)",
    )
)

_UTILITY_TOOL_HOSTS = {
    "dnssec-analyzer.verisignlabs.com",
}

def classify_article_quality_block(item: Any) -> ArticleQualityBlock | None:
    """Return a deterministic block for known article feed pollution."""
    if bool(getattr(item, "manual_added", False)):
        return None

    title = _safe_text(getattr(item, "title", None))
    url = _safe_text(getattr(item, "source_url", None)) or _safe_text(
        getattr(item, "canonical_url", None)
    )
    combined = f"{title} {url}"

    if any(pattern.search(combined) for pattern in _PUZZLE_PATTERNS) or any(
        pattern.search(url) for pattern in _PUZZLE_URL_PATTERNS
    ):
        return ArticleQualityBlock(
            reason="article_non_news_puzzle_help",
            detail="Recurring puzzle hints/answers are help content, not tech news.",
        )

    host = _hostname(url)
    if host in _UTILITY_TOOL_HOSTS:
        return ArticleQualityBlock(
            reason="article_utility_tool_page",
            detail="Utility/debugger output page is not an editorial news article.",
        )

    return None


def article_quality_sql_allow_filter():
    """SQL defense-in-depth filter matching the deterministic blockers."""
    title = func.lower(func.coalesce(ContentItem.title, ""))
    url = func.lower(func.coalesce(ContentItem.source_url, ContentItem.canonical_url, ""))
    source = func.lower(func.coalesce(ContentItem.source, ""))

    puzzle_terms = or_(
        title.like("%today%nyt%wordle%"),
        title.like("%today%nyt%connections%"),
        title.like("%today%nyt%strands%"),
        title.like("%today%nyt%mini crossword%"),
        title.like("%wordle hints%"),
        title.like("%wordle answers%"),
        title.like("%connections hints%"),
        title.like("%connections answers%"),
        title.like("%strands hints%"),
        title.like("%strands answers%"),
        title.like("%mini crossword answers%"),
        url.like("%/todays-nyt-wordle%"),
        url.like("%/todays-wordle%"),
        url.like("%/todays-nyt-connections%"),
        url.like("%/todays-nyt-strands%"),
        url.like("%/todays-nyt-mini-crossword%"),
    )
    utility_terms = or_(
        url.like("%dnssec-analyzer.verisignlabs.com%"),
        source == "dnssec-analyzer",
    )
    return or_(ContentItem.manual_added.is_(True), not_(or_(puzzle_terms, utility_terms)))


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _hostname(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()
