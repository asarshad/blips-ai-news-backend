"""Shared video relevance classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class VideoRelevanceDecision:
    """Normalized result of the Blips video relevance precheck."""

    is_relevant: bool
    confidence: float
    reason: str | None


def classify_video_blips_relevance(
    llm_client: Any,
    *,
    title: str,
    summary: str,
    source: str,
    url: Optional[str] = None,
) -> VideoRelevanceDecision | None:
    """Classify whether a video is relevant to the Blips tech-news audience."""
    if not settings.VIDEO_TECH_CLASSIFIER_ENABLED:
        return None

    try:
        result = llm_client.classify_blips_tech_relevance(
            title=title or "",
            summary=summary or "",
            source=source or "",
            url=url or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[video_relevance] classification failed title=%r source=%r: %s",
            (title or "")[:120],
            source or "",
            exc,
        )
        return None

    raw_relevance = str(getattr(result, "is_blips_tech_relevant", "") or "").strip().lower()
    if raw_relevance not in {"yes", "no"}:
        logger.warning(
            "[video_relevance] invalid classification result title=%r source=%r value=%r",
            (title or "")[:120],
            source or "",
            getattr(result, "is_blips_tech_relevant", None),
        )
        return None

    return VideoRelevanceDecision(
        is_relevant=raw_relevance == "yes",
        confidence=float(result.confidence),
        reason=result.reason,
    )
