"""Hybrid reranking for video feed candidates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import ceil
from typing import Dict, Iterable, List, Optional, Tuple

from app.integrations.youtube_channels import (
    ChannelRole,
    QualityTier,
    get_channel_by_name,
)
from app.models.content import ContentItem
from app.services.promotion_service import (
    compute_clickbait_penalty,
    compute_promotion_recency,
    compute_story_importance,
)

_ROLE_WEIGHT = {
    ChannelRole.OFFICIAL: 1.0,
    ChannelRole.NEWS: 0.95,
    ChannelRole.AI: 0.93,
    ChannelRole.ENGINEER: 0.92,
    ChannelRole.EXPLAINER: 0.88,
    ChannelRole.SHORTS: 0.82,
}

_QUALITY_WEIGHT = {
    QualityTier.PREMIUM: 1.0,
    QualityTier.STANDARD: 0.9,
    QualityTier.SUPPLEMENTAL: 0.75,
}

_RECENT_HOURS = 72.0
_RECENT_FLOOR_RATIO = 0.40
_MAX_PER_SOURCE = 4
_OFF_ROSTER_ESCAPE_MIN_PROMOTION = 0.34
_OFF_ROSTER_ESCAPE_MIN_GLOBAL = 0.32
_SHAPED_PREFIX_MULTIPLIER = 2
_MIN_SHAPED_PREFIX = 40


def _normalize_terms(values: object) -> List[str]:
    if not isinstance(values, list):
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            term = value.strip().lower()
        elif isinstance(value, dict):
            term = str(value.get("name") or value.get("value") or "").strip().lower()
        else:
            continue
        if not term or term in seen:
            continue
        normalized.append(term)
        seen.add(term)
    return normalized


def _story_graph(items: Iterable[ContentItem]) -> Tuple[Dict[str, int], Dict[str, int]]:
    topic_counts: Counter[str] = Counter()
    entity_counts: Counter[str] = Counter()
    for item in items:
        for topic in _normalize_terms(getattr(item, "topics", None))[:2]:
            topic_counts[topic] += 1
        for entity in _normalize_terms(getattr(item, "entities", None))[:3]:
            entity_counts[entity] += 1
    return dict(topic_counts), dict(entity_counts)


def _hours_old(item: ContentItem) -> Optional[float]:
    published_at = getattr(item, "published_at", None)
    if published_at is None:
        return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)


def _is_recent(item: ContentItem) -> bool:
    hours_old = _hours_old(item)
    return hours_old is not None and hours_old <= _RECENT_HOURS


def _curated_weight(item: ContentItem) -> Tuple[float, bool]:
    config = get_channel_by_name(getattr(item, "source", "") or "")
    if config is None:
        promotion = float(getattr(item, "promotion_score", 0.0) or 0.0)
        global_score = float(getattr(item, "global_score", 0.0) or 0.0)
        storyish = (
            promotion >= _OFF_ROSTER_ESCAPE_MIN_PROMOTION
            or global_score >= _OFF_ROSTER_ESCAPE_MIN_GLOBAL
        )
        if _is_recent(item) and storyish:
            return 0.78, False
        if _is_recent(item):
            return 0.56, False
        return 0.34, False

    weight = _ROLE_WEIGHT[config.role] * _QUALITY_WEIGHT[config.quality_tier]
    return weight, True


def _base_score(
    item: ContentItem,
    *,
    story_topic_counts: Dict[str, int],
    story_entity_counts: Dict[str, int],
) -> float:
    curated_weight, curated = _curated_weight(item)
    promotion = min(max(float(getattr(item, "promotion_score", 0.0) or 0.0), 0.0), 1.5)
    global_score = min(max(float(getattr(item, "global_score", 0.0) or 0.0), 0.0), 1.5)
    quality_score = min(max(float(getattr(item, "quality_score", 0.5) or 0.5), 0.0), 1.0)
    recency = (
        compute_promotion_recency(getattr(item, "published_at", None), 54.0)
        if getattr(item, "published_at", None) is not None
        else 0.0
    )
    story = compute_story_importance(item, story_topic_counts, story_entity_counts)
    clickbait = compute_clickbait_penalty(getattr(item, "title", "") or "")
    hours_old = _hours_old(item)

    score = (
        curated_weight * 0.26
        + promotion * 0.34
        + global_score * 0.10
        + recency * 0.16
        + story * 0.10
        + quality_score * 0.06
        - clickbait * 0.08
    )

    if curated:
        score += 0.05
    elif _is_recent(item):
        score += 0.04

    if hours_old is not None and hours_old > 96.0:
        score -= ((hours_old - 96.0) / 24.0) * 0.02

    return round(score, 6)


def rerank_video_candidates(items: List[ContentItem], *, target_count: int) -> List[ContentItem]:
    """Reorder promoted video candidates with curated-first but freshness-aware bias."""
    if not items:
        return []

    story_topic_counts, story_entity_counts = _story_graph(items)
    scored = [
        (
            item,
            _base_score(
                item,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            ),
        )
        for item in items
    ]
    scored.sort(
        key=lambda pair: (
            pair[1],
            getattr(pair[0], "promotion_score", 0.0) or 0.0,
            getattr(pair[0], "global_score", 0.0) or 0.0,
            getattr(pair[0], "published_at", datetime.min),
        ),
        reverse=True,
    )

    shaped_target = min(
        len(scored), max(target_count * _SHAPED_PREFIX_MULTIPLIER, _MIN_SHAPED_PREFIX)
    )
    recent_floor = min(
        sum(1 for item, _score in scored if _is_recent(item)),
        ceil(min(target_count, shaped_target) * _RECENT_FLOOR_RATIO),
    )

    selected: List[ContentItem] = []
    used_ids: set[int] = set()
    per_source: Counter[str] = Counter()
    recent_selected = 0

    while len(selected) < shaped_target:
        need_recent = len(selected) < recent_floor and recent_selected < recent_floor
        chosen: Optional[ContentItem] = None
        chosen_score = float("-inf")

        for item, score in scored:
            if item.id in used_ids:
                continue
            source = getattr(item, "source", "") or "unknown"
            if per_source[source] >= _MAX_PER_SOURCE:
                continue
            if need_recent and not _is_recent(item):
                continue
            adjusted = score - min(max(per_source[source] - 1, 0), 4) * 0.03
            if adjusted > chosen_score:
                chosen = item
                chosen_score = adjusted

        if chosen is None and need_recent:
            # Not enough recent candidates; relax the recent floor only.
            recent_floor = len(selected)
            continue

        if chosen is None:
            # Relax source cap to avoid starving the feed.
            for item, _score in scored:
                if item.id in used_ids:
                    continue
                chosen = item
                break

        if chosen is None:
            break

        selected.append(chosen)
        used_ids.add(chosen.id)
        source = getattr(chosen, "source", "") or "unknown"
        per_source[source] += 1
        if _is_recent(chosen):
            recent_selected += 1

    remainder = [item for item, _score in scored if item.id not in used_ids]
    return selected + remainder
