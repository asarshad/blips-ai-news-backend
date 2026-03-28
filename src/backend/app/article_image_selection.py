"""Shared article image candidate selection across ingestion and repair paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.extraction.metadata import is_probably_generic_image_url
from app.extraction.normalize import is_suspicious_image_url, validate_image_url

_EDITORIAL_URL_KEYWORDS = (
    "hero",
    "feature",
    "featured",
    "cover",
    "lead",
    "header",
    "story",
    "article",
    "post",
)
_WEAK_URL_KEYWORDS = (
    "thumb",
    "thumbnail",
    "avatar",
    "logo",
    "icon",
    "small",
    "sprite",
)
_SOURCE_BASE_SCORES = {
    "extraction": 100.0,
    "page_metadata": 92.0,
    "page_metadata_body": 92.0,
    "page_metadata_og": 74.0,
    "page_metadata_twitter": 72.0,
    "page_metadata_other": 76.0,
    "prepared": 90.0,
    "rss_content": 84.0,
    "rss_summary": 82.0,
    "rss_description": 80.0,
    "rss": 78.0,
    "rss_media_content": 76.0,
    "rss_enclosure": 74.0,
    "rss_media_thumbnail": 68.0,
    "llm_extract": 88.0,
    "direct": 72.0,
    "existing": 86.0,
}


@dataclass(frozen=True)
class ArticleImageCandidate:
    """One candidate image URL plus the path that produced it."""

    url: Optional[str]
    source: str


@dataclass(frozen=True)
class RankedArticleImageCandidate:
    """Validated candidate with its computed selection score."""

    url: str
    source: str
    score: float


def page_metadata_candidate_source(image_source: Optional[str]) -> str:
    """Return a selector source label that reflects the page metadata origin."""
    normalized = (image_source or "").strip().lower()
    if normalized == "body":
        return "page_metadata_body"
    if normalized == "og":
        return "page_metadata_og"
    if normalized == "twitter":
        return "page_metadata_twitter"
    if normalized:
        return "page_metadata_other"
    return "page_metadata"


def rank_article_image_candidates(
    candidates: Iterable[ArticleImageCandidate],
) -> list[RankedArticleImageCandidate]:
    """Validate, dedupe, and score article image candidates."""
    best_by_url: dict[str, RankedArticleImageCandidate] = {}

    for candidate in candidates:
        normalized = validate_image_url((candidate.url or "").strip())
        if not normalized:
            continue
        if is_probably_generic_image_url(normalized) or is_suspicious_image_url(normalized):
            continue

        ranked = RankedArticleImageCandidate(
            url=normalized,
            source=candidate.source,
            score=_score_article_image_candidate(normalized, candidate.source),
        )
        current = best_by_url.get(normalized)
        if current is None or ranked.score > current.score:
            best_by_url[normalized] = ranked

    return sorted(
        best_by_url.values(),
        key=lambda candidate: (candidate.score, candidate.source != "existing"),
        reverse=True,
    )


def select_best_article_image(candidates: Iterable[ArticleImageCandidate]) -> Optional[str]:
    """Return the strongest validated editorial image, or None."""
    ranked = rank_article_image_candidates(candidates)
    if not ranked:
        return None
    return ranked[0].url


def _score_article_image_candidate(url: str, source: str) -> float:
    score = _SOURCE_BASE_SCORES.get(source, _SOURCE_BASE_SCORES["rss"])
    lowered = url.lower()

    if any(keyword in lowered for keyword in _EDITORIAL_URL_KEYWORDS):
        score += 6.0
    if any(keyword in lowered for keyword in _WEAK_URL_KEYWORDS):
        score -= 10.0
    if lowered.endswith(".gif"):
        score -= 4.0

    return score
