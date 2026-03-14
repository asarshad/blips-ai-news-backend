"""Offline validation helpers for challenger video/reel ranking strategies."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.ingestion.language_filter import detect_language
from app.integrations.youtube_channels import (
    ChannelConfig,
    ChannelRole,
    QualityTier,
    get_channel_by_name,
)
from app.services.promotion_service import (
    compute_category_gap_bonus,
    compute_clickbait_penalty,
    compute_creator_fatigue_penalty,
    compute_promotion_recency,
    compute_story_importance,
)

_TECH_KEYWORDS = (
    "ai",
    "android",
    "apple",
    "app store",
    "benchmark",
    "camera",
    "chatgpt",
    "chip",
    "claude",
    "cloud",
    "copilot",
    "cpu",
    "developer",
    "docker",
    "galaxy",
    "gemini",
    "github",
    "google",
    "gpu",
    "hands on",
    "iphone",
    "ipad",
    "ios",
    "kubernetes",
    "launch",
    "laptop",
    "linux",
    "llm",
    "mac",
    "macbook",
    "meta",
    "microsoft",
    "model",
    "navigation",
    "openai",
    "pixel",
    "privacy",
    "python",
    "release",
    "review",
    "samsung",
    "security",
    "silicon",
    "smartphone",
    "software",
    "startup",
    "surface",
    "tesla",
    "typescript",
    "update",
    "vision pro",
    "windows",
)

_OFF_TOPIC_KEYWORDS = (
    "asmr",
    "challenge",
    "dance",
    "family",
    "food",
    "funny",
    "lyric",
    "makeup",
    "mukbang",
    "prank",
    "recipe",
    "short film",
    "skit",
    "song",
    "travel",
    "vlog",
    "wedding",
    "workout",
)

_ROLE_WEIGHT = {
    ChannelRole.OFFICIAL: 1.0,
    ChannelRole.NEWS: 0.94,
    ChannelRole.AI: 0.92,
    ChannelRole.ENGINEER: 0.90,
    ChannelRole.EXPLAINER: 0.88,
    ChannelRole.SHORTS: 0.82,
}

_QUALITY_WEIGHT = {
    QualityTier.PREMIUM: 1.0,
    QualityTier.STANDARD: 0.88,
    QualityTier.SUPPLEMENTAL: 0.72,
}


@dataclass(frozen=True)
class ChallengerConfig:
    """Surface-specific challenger scoring and diversity controls."""

    source_weight: float
    story_weight: float
    recency_weight: float
    quality_weight: float
    trend_weight: float
    tech_weight: float
    boost_weight: float
    clickbait_weight: float
    recency_half_life_hours: float
    min_tech_score: float
    max_per_source: int
    category_gap_scale: float
    creator_penalty_scale: float
    fresh_story_bonus: float
    fresh_story_window_hours: float
    stale_after_hours: float
    stale_penalty_per_day: float
    recent_floor_ratio: float
    recent_floor_hours: float
    recent_slot_bonus: float


_VIDEO_CHALLENGER = ChallengerConfig(
    source_weight=0.26,
    story_weight=0.18,
    recency_weight=0.24,
    quality_weight=0.12,
    trend_weight=0.04,
    tech_weight=0.10,
    boost_weight=0.04,
    clickbait_weight=0.10,
    recency_half_life_hours=54.0,
    min_tech_score=0.18,
    max_per_source=4,
    category_gap_scale=1.0,
    creator_penalty_scale=0.08,
    fresh_story_bonus=0.12,
    fresh_story_window_hours=72.0,
    stale_after_hours=84.0,
    stale_penalty_per_day=0.03,
    recent_floor_ratio=0.45,
    recent_floor_hours=72.0,
    recent_slot_bonus=0.10,
)

_REEL_CHALLENGER = ChallengerConfig(
    source_weight=0.30,
    story_weight=0.14,
    recency_weight=0.18,
    quality_weight=0.10,
    trend_weight=0.04,
    tech_weight=0.16,
    boost_weight=0.04,
    clickbait_weight=0.12,
    recency_half_life_hours=30.0,
    min_tech_score=0.35,
    max_per_source=2,
    category_gap_scale=0.75,
    creator_penalty_scale=0.18,
    fresh_story_bonus=0.05,
    fresh_story_window_hours=48.0,
    stale_after_hours=60.0,
    stale_penalty_per_day=0.05,
    recent_floor_ratio=0.30,
    recent_floor_hours=48.0,
    recent_slot_bonus=0.04,
)


@dataclass(frozen=True)
class ValidationItem:
    """Minimal item representation for offline strategy evaluation."""

    id: int
    surface: str
    title: str
    source: str
    source_url: str
    published_at: Optional[datetime] = None
    description: str = ""
    summary: str = ""
    topics: Sequence[Any] = ()
    entities: Sequence[Any] = ()
    quality_score: Optional[float] = None
    trend_score: Optional[float] = None
    global_score: Optional[float] = None
    editorial_boost: int = 0


@dataclass(frozen=True)
class RankedItem:
    """One challenger-ranked item with diagnostics."""

    item: ValidationItem
    score: float
    source_config: ChannelConfig
    story_score: float
    tech_score: float
    clickbait_penalty: float


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


def _primary_topic(item: ValidationItem) -> Optional[str]:
    topics = _normalize_terms(item.topics)
    return topics[0] if topics else None


def _quality_component(item: ValidationItem) -> float:
    try:
        value = float(item.quality_score)
    except (TypeError, ValueError):
        return 0.5
    return min(max(value, 0.0), 1.0)


def _trend_component(item: ValidationItem) -> float:
    for value in (item.trend_score, item.global_score):
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        return min(max(score, 0.0), 1.0)
    return 0.0


def _story_graph(items: Iterable[ValidationItem]) -> tuple[Dict[str, int], Dict[str, int]]:
    topic_counts: Counter[str] = Counter()
    entity_counts: Counter[str] = Counter()
    for item in items:
        for topic in _normalize_terms(item.topics)[:2]:
            topic_counts[topic] += 1
        for entity in _normalize_terms(item.entities)[:3]:
            entity_counts[entity] += 1
    return dict(topic_counts), dict(entity_counts)


def strict_title_is_english(title: str) -> bool:
    """Use title-only detection to avoid English descriptions diluting the result."""
    stripped = (title or "").strip()
    if not stripped:
        return True

    for char in stripped:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        if "LATIN" not in name:
            return False

    lang, confidence = detect_language(stripped)
    if lang is None:
        return True
    if lang == "en":
        return True
    return confidence < 0.95


def tech_signal_score(item: ValidationItem, source_config: Optional[ChannelConfig] = None) -> float:
    """Estimate whether an item is likely to be substantive tech content."""
    text = " ".join(
        [
            item.title,
            item.summary,
            item.description,
            *(_normalize_terms(item.topics)[:4]),
            *(_normalize_terms(item.entities)[:5]),
        ]
    ).lower()
    if not text:
        return 0.0

    keyword_hits = sum(1 for token in _TECH_KEYWORDS if token in text)
    off_topic_hits = sum(1 for token in _OFF_TOPIC_KEYWORDS if token in text)

    if source_config and source_config.role in {
        ChannelRole.OFFICIAL,
        ChannelRole.AI,
        ChannelRole.ENGINEER,
        ChannelRole.NEWS,
    }:
        base = 0.45
    elif source_config is not None:
        base = 0.25
    else:
        base = 0.0

    score = base + min(keyword_hits, 6) * 0.12 - min(off_topic_hits, 3) * 0.18
    return round(min(max(score, 0.0), 1.0), 4)


def is_curated_trusted(item: ValidationItem) -> bool:
    return get_channel_by_name(item.source or "") is not None


def _surface_config(surface: str) -> ChallengerConfig:
    return _REEL_CHALLENGER if surface == "reels" else _VIDEO_CHALLENGER


def _hours_old(item: ValidationItem) -> Optional[float]:
    if item.published_at is None:
        return None
    published_at = item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)


def _is_recent(item: ValidationItem, *, max_hours: float) -> bool:
    hours_old = _hours_old(item)
    return hours_old is not None and hours_old <= max_hours


def _base_score(
    item: ValidationItem,
    *,
    source_config: ChannelConfig,
    config: ChallengerConfig,
    story_topic_counts: Dict[str, int],
    story_entity_counts: Dict[str, int],
) -> tuple[float, float, float, float]:
    story_score = compute_story_importance(item, story_topic_counts, story_entity_counts)
    tech_score = tech_signal_score(item, source_config)
    clickbait_penalty = compute_clickbait_penalty(item.title or "")

    source_weight = _QUALITY_WEIGHT[source_config.quality_tier] * _ROLE_WEIGHT[source_config.role]
    recency = (
        compute_promotion_recency(
            item.published_at,
            config.recency_half_life_hours,
        )
        if item.published_at
        else 0.0
    )
    quality = _quality_component(item)
    trend = _trend_component(item)
    boost = min(max(item.editorial_boost, 0), 3) / 3.0
    hours_old = _hours_old(item)
    fresh_bonus = 0.0
    stale_penalty = 0.0

    if hours_old is not None:
        if hours_old <= config.fresh_story_window_hours:
            freshness_ratio = 1.0 - (hours_old / max(config.fresh_story_window_hours, 1.0))
            fresh_bonus = config.fresh_story_bonus * freshness_ratio * max(story_score, tech_score)
        if hours_old > config.stale_after_hours:
            days_over = (hours_old - config.stale_after_hours) / 24.0
            stale_penalty = config.stale_penalty_per_day * days_over

    score = (
        source_weight * config.source_weight
        + story_score * config.story_weight
        + recency * config.recency_weight
        + quality * config.quality_weight
        + trend * config.trend_weight
        + tech_score * config.tech_weight
        + boost * config.boost_weight
        + fresh_bonus
        - clickbait_penalty * config.clickbait_weight
        - stale_penalty
    )
    return round(score, 4), story_score, tech_score, clickbait_penalty


def challenger_rank(
    items: Sequence[ValidationItem],
    *,
    surface: str,
    limit: int = 50,
) -> tuple[List[RankedItem], Dict[str, int]]:
    """Greedy rerank over a weekly curated pool using source/story/quality heuristics."""
    config = _surface_config(surface)
    story_topic_counts, story_entity_counts = _story_graph(items)

    eligible: List[tuple[ValidationItem, ChannelConfig]] = []
    excluded = Counter()
    for item in items:
        source_config = get_channel_by_name(item.source or "")
        if source_config is None:
            excluded["off_roster"] += 1
            continue
        if not strict_title_is_english(item.title):
            excluded["non_english_title"] += 1
            continue
        tech_score = tech_signal_score(item, source_config)
        if tech_score < config.min_tech_score:
            excluded["weak_tech_fit"] += 1
            continue
        eligible.append((item, source_config))

    selected: List[RankedItem] = []
    selected_by_source: Counter[str] = Counter()
    selected_by_topic: Counter[str] = Counter()
    selected_recent_count = 0
    remaining = list(eligible)
    recent_floor_count = ceil(limit * config.recent_floor_ratio)

    while remaining and len(selected) < limit:
        best_index = -1
        best_score = float("-inf")
        best_ranked: Optional[RankedItem] = None
        need_recent = (
            recent_floor_count > 0
            and len(selected) < recent_floor_count
            and selected_recent_count < recent_floor_count
        )

        for index, (item, source_config) in enumerate(remaining):
            if selected_by_source[item.source] >= config.max_per_source:
                continue
            base_score, story_score, tech_score, clickbait_penalty = _base_score(
                item,
                source_config=source_config,
                config=config,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            )
            topic = _primary_topic(item)
            category_gap = compute_category_gap_bonus(topic, dict(selected_by_topic))
            creator_penalty = compute_creator_fatigue_penalty(selected_by_source[item.source])
            adjusted = (
                base_score
                + category_gap * config.category_gap_scale
                - creator_penalty * config.creator_penalty_scale
            )
            if need_recent:
                adjusted += (
                    config.recent_slot_bonus
                    if _is_recent(item, max_hours=config.recent_floor_hours)
                    else -config.recent_slot_bonus
                )

            if adjusted > best_score:
                best_score = adjusted
                best_index = index
                best_ranked = RankedItem(
                    item=item,
                    score=round(adjusted, 4),
                    source_config=source_config,
                    story_score=story_score,
                    tech_score=tech_score,
                    clickbait_penalty=clickbait_penalty,
                )

        if best_index < 0 and surface == "videos":
            for index, (item, source_config) in enumerate(remaining):
                base_score, story_score, tech_score, clickbait_penalty = _base_score(
                    item,
                    source_config=source_config,
                    config=config,
                    story_topic_counts=story_topic_counts,
                    story_entity_counts=story_entity_counts,
                )
                topic = _primary_topic(item)
                category_gap = compute_category_gap_bonus(topic, dict(selected_by_topic))
                creator_penalty = compute_creator_fatigue_penalty(selected_by_source[item.source])
                adjusted = (
                    base_score
                    + category_gap * config.category_gap_scale
                    - creator_penalty * config.creator_penalty_scale
                )
                if need_recent:
                    adjusted += (
                        config.recent_slot_bonus
                        if _is_recent(item, max_hours=config.recent_floor_hours)
                        else -config.recent_slot_bonus
                    )

                if adjusted > best_score:
                    best_score = adjusted
                    best_index = index
                    best_ranked = RankedItem(
                        item=item,
                        score=round(adjusted, 4),
                        source_config=source_config,
                        story_score=story_score,
                        tech_score=tech_score,
                        clickbait_penalty=clickbait_penalty,
                    )

        if best_index < 0 or best_ranked is None:
            break

        selected.append(best_ranked)
        selected_by_source[best_ranked.item.source] += 1
        topic = _primary_topic(best_ranked.item)
        if topic:
            selected_by_topic[topic] += 1
        if _is_recent(best_ranked.item, max_hours=config.recent_floor_hours):
            selected_recent_count += 1
        remaining.pop(best_index)

    return selected, dict(excluded)


def summarize_ranked(items: Sequence[ValidationItem | RankedItem]) -> Dict[str, Any]:
    """Summarize hygiene and diversity metrics for a ranked list."""
    normalized: List[ValidationItem] = [
        ranked.item if isinstance(ranked, RankedItem) else ranked for ranked in items
    ]
    if not normalized:
        return {
            "count": 0,
            "curated_share_pct": 0.0,
            "premium_share_pct": 0.0,
            "non_english_titles": 0,
            "weak_tech_fit_count": 0,
            "median_age_hours": None,
            "distinct_sources": 0,
            "dominant_source_pct": 0.0,
            "avg_clickbait_penalty": 0.0,
            "role_coverage": [],
        }

    now = datetime.now(timezone.utc)
    ages: List[float] = []
    source_counts: Counter[str] = Counter()
    curated = 0
    premium = 0
    non_english = 0
    weak_tech = 0
    clickbait_values: List[float] = []
    roles: set[str] = set()

    for item in normalized:
        cfg = get_channel_by_name(item.source or "")
        if cfg is not None:
            curated += 1
            roles.add(cfg.role.value)
            if cfg.quality_tier == QualityTier.PREMIUM:
                premium += 1
        if not strict_title_is_english(item.title):
            non_english += 1
        if tech_signal_score(item, cfg) < _surface_config(item.surface).min_tech_score:
            weak_tech += 1
        clickbait_values.append(compute_clickbait_penalty(item.title or ""))
        source_counts[item.source or "unknown"] += 1

        if item.published_at is not None:
            published_at = item.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            ages.append(max(0.0, (now - published_at).total_seconds() / 3600))

    dominant_source_pct = 0.0
    if source_counts:
        dominant_source_pct = max(source_counts.values()) / len(normalized) * 100.0

    return {
        "count": len(normalized),
        "curated_share_pct": round(curated / len(normalized) * 100.0, 2),
        "premium_share_pct": round(premium / len(normalized) * 100.0, 2),
        "non_english_titles": non_english,
        "weak_tech_fit_count": weak_tech,
        "median_age_hours": round(median(ages), 2) if ages else None,
        "distinct_sources": len(source_counts),
        "dominant_source_pct": round(dominant_source_pct, 2),
        "avg_clickbait_penalty": round(sum(clickbait_values) / len(clickbait_values), 4),
        "role_coverage": sorted(roles),
    }
