"""Promotion Service — Quality Gate for the two-tier pipeline.

Responsibilities:
1. Score all CANDIDATE content items using multi-factor promotion scoring.
2. Promote the top-N items per rolling window to PROMOTED status.
3. Persist promotion_score on every evaluated item (for observability).

Promotion Score Formula (all components 0-1 before weighting):

    promotion_score = (
        W_SOURCE   * source_quality        # publisher trust
      + W_CLUSTER  * cluster_hotness       # multi-source coverage + signal hits
      + W_RECENCY  * recency               # freshness is a boost, not a hard gate
      + W_VELOCITY * momentum              # age-normalized demand
      + W_STORY    * story_importance      # launch/update/review relevance over a 7-day pool
      - W_CLICKBAIT * clickbait_penalty    # title quality gate
      - W_DEDUP    * duplicate_penalty     # cluster crowding penalty
    )

Defaults:
    W_SOURCE / W_CLUSTER / W_RECENCY remain surface-specific.
    Videos and reels additionally use velocity, format-fit, category-gap,
    creator-fatigue, and story-importance components.

Promotion threshold:  PROMOTE_MIN_SCORE  (default 0.30)
Max promoted per run: TOP_N_PROMOTED     (default 50 per content type per 6-hour window)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.integrations.youtube_channels import (
    ChannelConfig,
    ChannelRole,
    ContentFormat,
    get_channel_by_id,
    get_channel_by_name,
)
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.video_source import VideoSourceProfile
from app.ranking.quality import compute_source_weight
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.content_readiness import sync_content_readiness
from app.services.video_content_policy import apply_content_policy

logger = get_logger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromotionConfig:
    """Tunable weights and thresholds for the promotion scorer."""

    # Score component weights (must sum to 1 when penalties are zero)
    w_source: float = 0.25
    w_cluster: float = 0.30
    w_recency: float = 0.25
    w_clickbait: float = 0.10
    w_duplicate: float = 0.10
    w_velocity: float = 0.0
    w_format_fit: float = 0.0
    w_category_gap: float = 0.0
    w_creator_fatigue: float = 0.0
    w_story: float = 0.0

    # Minimum promotion score to be promoted
    min_score: float = 0.30

    # Max items promoted per type per promotion run
    top_n_per_type: int = 50

    # Rolling window (hours) to evaluate candidates over
    window_hours: int = 48

    # Recency half-life (hours) for exponential decay
    recency_half_life_hours: float = 12.0

    # Maximum signal_hits bonus (caps contribution at this value)
    signal_hits_cap: int = 5
    discovery_lane_penalty: float = 0.0


_DEFAULT_CONFIG = PromotionConfig()
_VIDEO_CONFIG = PromotionConfig(
    w_source=0.18,
    w_cluster=0.14,
    w_recency=0.10,
    w_clickbait=0.09,
    w_duplicate=0.07,
    w_velocity=0.13,
    w_format_fit=0.08,
    w_category_gap=0.05,
    w_creator_fatigue=0.04,
    w_story=0.16,
    min_score=0.30,
    top_n_per_type=80,
    window_hours=168,
    recency_half_life_hours=72.0,
    discovery_lane_penalty=0.03,
)
_REEL_CONFIG = PromotionConfig(
    w_source=0.14,
    w_cluster=0.10,
    w_recency=0.14,
    w_clickbait=0.08,
    w_duplicate=0.05,
    w_velocity=0.12,
    w_format_fit=0.10,
    w_category_gap=0.04,
    w_creator_fatigue=0.05,
    w_story=0.22,
    min_score=0.36,
    top_n_per_type=120,
    window_hours=168,
    recency_half_life_hours=48.0,
    discovery_lane_penalty=0.10,
)


# ── Clickbait detection ───────────────────────────────────────────────────────

# Regex pattern list – case-insensitive
_CLICKBAIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"you won't believe",
        r"shocking(?:ly)?",
        r"mind.?blow",
        r"this one (?:trick|tip|weird)",
        r"(?:top|best)\s+\d+\s+(?:ways|tips|tricks|secrets|hacks)",
        r"(?:doctors?|experts?|scientists?)\s+(?:hate|love|don't want you)",
        r"what happens next",
        r"gone (?:wrong|viral|crazy)",
        r"can'?t believe",
        r"secret(?:s)? (?:they|nobody|no one)",
        r"clickbait",
        r"!{3,}",  # Three or more exclamation marks
        r"\?{2,}",  # Two or more question marks
    ]
]

# ALLCAPS title check: more than 40 % of alpha chars uppercase
_ALLCAPS_THRESHOLD = 0.4
_STORY_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\blaunch(?:ed|es|ing)?\b",
        r"\bannounce(?:d|ment|s)?\b",
        r"\bunveil(?:ed|s|ing)?\b",
        r"\bkeynote\b",
        r"\bhands?\s+on\b",
        r"\bfirst\s+look\b",
        r"\breview\b",
        r"\bbenchmark(?:ed|ing|s)?\b",
        r"\bcomparison\b",
        r"\bvs\.?\b",
        r"\brelease(?:d|s)?\b",
        r"\bshipping\b",
        r"\bavailable\s+now\b",
        r"\bupdate\b",
    ]
]
_TECH_SIGNAL_TOKENS = (
    "ai",
    "android",
    "api",
    "app",
    "apple",
    "benchmark",
    "camera",
    "chatgpt",
    "chip",
    "claude",
    "cloud",
    "code",
    "copilot",
    "developer",
    "device",
    "fitness tracker",
    "galaxy",
    "gemini",
    "github",
    "gpt",
    "gpu",
    "hands on",
    "hardware",
    "health tech",
    "iphone",
    "linux",
    "mac",
    "openai",
    "phone",
    "pixel",
    "privacy",
    "review",
    "security",
    "software",
    "tracker",
    "update",
    "wearable",
)
_OFF_TOPIC_SIGNAL_TOKENS = (
    "audio only",
    "campaign",
    "election",
    "family",
    "mad money",
    "manager",
    "policy",
    "president trump",
    "prank",
    "reaction",
    "senate",
    "skate park",
    "splashdown",
    "summit",
    "tariff",
    "travel",
    "vlog",
    "white house",
)
_VIDEO_LEAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\baudio\s+only\b",
        r"\bmad\s+money\b",
        r"\bwhite\s+house\b",
        r"\bpresident\s+trump\b",
        r"\bvp\s+jd\s+vance\b",
        r"\bchina\s+summit\b",
    ]
]
_BROAD_NEWS_SOURCE_NAMES = {
    "reuters",
    "bloomberg technology",
    "cnbc television",
    "the wall street journal",
}
_BROAD_NEWS_LEAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^live:",
        r"\blive\b",
        r"\bbriefing\b",
        r"\bcommittee\b",
        r"\bcongress\b",
        r"\bhouse\s+oversight\b",
        r"\bwhite\s+house\b",
        r"\bpresident\b",
        r"\bimmigration\b",
        r"\belection\b",
        r"\bcampaign\b",
        r"\bmaga\b",
        r"\bfed\b",
        r"\bfomc\b",
        r"\brates?\b",
        r"\bhawkish\b",
        r"\binflation\b",
        r"\bmarkets?\b",
        r"\bcrypto\s+world\b",
    ]
]


def compute_clickbait_penalty(title: str) -> float:
    """Return a penalty [0.0, 1.0] based on clickbait signals in the title.

    0.0 = clean title, 1.0 = maximum clickbait.
    """
    if not title:
        return 0.0

    score = 0.0

    # Pattern hits
    for pat in _CLICKBAIT_PATTERNS:
        if pat.search(title):
            score += 0.20

    # All-caps ratio
    alpha = [c for c in title if c.isalpha()]
    if alpha:
        upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
        if upper_ratio > _ALLCAPS_THRESHOLD:
            score += 0.30

    # Title length extremes (very short < 10 chars OR very long > 200 chars)
    if len(title) < 10 or len(title) > 200:
        score += 0.10

    return min(score, 1.0)


# ── Recency score ─────────────────────────────────────────────────────────────


def compute_promotion_recency(published_at: datetime, half_life_hours: float) -> float:
    """Exponential decay recency score: 1.0 when fresh, 0 → stale."""
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    hours_old = max(0.0, (now - published_at).total_seconds() / 3600)
    return math.pow(0.5, hours_old / half_life_hours)


# ── Cluster hotness ───────────────────────────────────────────────────────────


def compute_cluster_hotness(
    cluster_id: Optional[str],
    signal_hits: int,
    cluster_sizes: Dict[str, int],
    signal_hits_cap: int,
) -> float:
    """Combine cluster size (multi-source coverage) with signal hits [0, 1]."""
    # Cluster coverage: log-scaled number of items in the same cluster
    cluster_size = cluster_sizes.get(cluster_id or "", 1)
    # log2(1)=0 ... log2(8)=3; cap to [0,1] where 8+ items = fully hot
    cluster_component = min(math.log2(max(cluster_size, 1)) / 3.0, 1.0)

    # Signal component: normalise signal_hits to [0, 1]
    signal_component = min(signal_hits, signal_hits_cap) / signal_hits_cap

    # Equal weight between cluster coverage and signal hits
    return (cluster_component + signal_component) / 2.0


# ── Duplicate density penalty ─────────────────────────────────────────────────


def compute_duplicate_penalty(cluster_id: Optional[str], cluster_sizes: Dict[str, int]) -> float:
    """Penalise items in over-crowded clusters to avoid showing redundant news.

    cluster_size=1 → 0.0 penalty
    cluster_size=10+ → 0.5 max penalty
    """
    size = cluster_sizes.get(cluster_id or "", 1)
    if size <= 2:
        return 0.0
    # Soft cap: log-scale so very large clusters get a moderate penalty
    return min(math.log2(size - 1) / 4.0, 0.5)


def compute_velocity_score(views_per_hour: Optional[float]) -> float:
    """Log-normalize views/hour into a [0,1] velocity signal."""
    try:
        value = float(views_per_hour)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return min(math.log10(value + 1) / 5.0, 1.0)


def compute_category_gap_bonus(
    topic: Optional[str],
    promoted_topic_counts: Dict[str, int],
) -> float:
    """Boost underrepresented categories within the recent promoted set."""
    if not topic:
        return 0.0
    total = sum(promoted_topic_counts.values())
    if total <= 0:
        return 0.05
    share = promoted_topic_counts.get(topic, 0) / max(total, 1)
    if share < 0.08:
        return 0.08
    if share < 0.15:
        return 0.04
    return 0.0


def compute_creator_fatigue_penalty(channel_count: int) -> float:
    """Penalize channels already occupying a large share of the feed."""
    if channel_count <= 1:
        return 0.0
    return min((channel_count - 1) / 4.0, 1.0)


def _normalize_metadata_terms(values: object) -> List[str]:
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


def compute_story_overlap(terms: List[str], story_counts: Dict[str, int]) -> float:
    """Measure how strongly an item overlaps with the recent 7-day story graph."""
    if not terms or not story_counts:
        return 0.0

    strongest = max(story_counts.get(term, 0) for term in terms)
    if strongest <= 0:
        return 0.0

    peak = max(story_counts.values()) or 1
    return min(math.log1p(strongest) / math.log1p(peak), 1.0)


def compute_story_keyword_signal(title: str, description: str = "") -> float:
    """Recognize launch/review/update framing that matters before engagement accumulates."""
    text = f"{title} {description}".strip().lower()
    if not text:
        return 0.0

    hits = sum(1 for pattern in _STORY_SIGNAL_PATTERNS if pattern.search(text))
    if hits <= 0:
        return 0.0
    return min(0.25 + hits * 0.15, 1.0)


def _safe_float(value: Optional[float], default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Optional[str]) -> str:
    return value.lower() if isinstance(value, str) else ""


def compute_story_importance(
    item: ContentItem,
    story_topic_counts: Dict[str, int],
    story_entity_counts: Dict[str, int],
) -> float:
    """Blend story overlap with event framing so important launches can cold-start strongly."""
    topics = _normalize_metadata_terms(getattr(item, "topics", None))[:3]
    entities = _normalize_metadata_terms(getattr(item, "entities", None))[:4]
    topic_overlap = compute_story_overlap(topics, story_topic_counts)
    entity_overlap = compute_story_overlap(entities, story_entity_counts)
    description = (
        getattr(item, "description", "")
        if isinstance(getattr(item, "description", None), str)
        else ""
    )
    summary = (
        getattr(item, "summary", "") if isinstance(getattr(item, "summary", None), str) else ""
    )
    keyword_signal = compute_story_keyword_signal(
        getattr(item, "title", "") if isinstance(getattr(item, "title", None), str) else "",
        description or summary,
    )

    score = min(1.0, entity_overlap * 0.45 + topic_overlap * 0.25 + keyword_signal * 0.30)

    published_at = getattr(item, "published_at", None)
    if isinstance(published_at, datetime):
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        hours_old = max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
        if hours_old <= 24 and score >= 0.30:
            cold_start_bonus = 0.12 * (
                1.0 - compute_velocity_score(getattr(item, "views_per_hour", None))
            )
            score = min(1.0, score + cold_start_bonus)

    return round(score, 4)


def _channel_config_for_item(item: ContentItem) -> ChannelConfig | None:
    channel_id = getattr(item, "channel_id", None)
    if isinstance(channel_id, str) and channel_id:
        config = get_channel_by_id(channel_id)
        if config is not None:
            return config
    source = getattr(item, "source", None)
    if isinstance(source, str) and source:
        return get_channel_by_name(source)
    return None


def _resolve_channel_role(
    channel_config: ChannelConfig | None,
    source_profile: VideoSourceProfile | None,
) -> ChannelRole | None:
    if channel_config is not None:
        return channel_config.role
    if source_profile is not None and source_profile.role:
        try:
            return ChannelRole(source_profile.role)
        except ValueError:
            return None
    return None


def _resolve_content_format(
    channel_config: ChannelConfig | None,
    source_profile: VideoSourceProfile | None,
) -> ContentFormat | None:
    if channel_config is not None:
        return channel_config.content_format
    if source_profile is not None and source_profile.content_format:
        try:
            return ContentFormat(source_profile.content_format)
        except ValueError:
            return None
    return None


def _is_broad_news_source(
    item: ContentItem,
    channel_config: ChannelConfig | None,
    source_profile: VideoSourceProfile | None,
) -> bool:
    if channel_config is not None:
        source_name = channel_config.name
    elif source_profile is not None:
        source_name = source_profile.channel_name
    else:
        source_name = getattr(item, "source", None)
    if not isinstance(source_name, str):
        return False
    return source_name.strip().lower() in _BROAD_NEWS_SOURCE_NAMES


def compute_editorial_tech_score(
    item: ContentItem,
    *,
    channel_role: ChannelRole | None = None,
) -> float:
    """Estimate whether an item looks like substantive tech content."""
    title = getattr(item, "title", "") if isinstance(getattr(item, "title", None), str) else ""
    summary = (
        getattr(item, "summary", "") if isinstance(getattr(item, "summary", None), str) else ""
    )
    description = (
        getattr(item, "description", "")
        if isinstance(getattr(item, "description", None), str)
        else ""
    )
    terms = " ".join(
        [
            title,
            summary,
            description,
            *(_normalize_metadata_terms(getattr(item, "topics", None))[:4]),
            *(_normalize_metadata_terms(getattr(item, "entities", None))[:5]),
        ]
    ).lower()
    if not terms:
        return 0.0

    keyword_hits = sum(1 for token in _TECH_SIGNAL_TOKENS if token in terms)
    off_topic_hits = sum(1 for token in _OFF_TOPIC_SIGNAL_TOKENS if token in terms)

    if channel_role in {ChannelRole.AI, ChannelRole.ENGINEER}:
        base = 0.20
    elif channel_role == ChannelRole.OFFICIAL:
        base = 0.18
    elif channel_role == ChannelRole.NEWS:
        base = 0.12
    elif channel_role == ChannelRole.SHORTS:
        base = 0.15
    else:
        base = 0.08

    score = base + min(keyword_hits, 6) * 0.10 - min(off_topic_hits, 3) * 0.20
    return round(min(max(score, 0.0), 1.0), 4)


def classify_promotion_block(
    item: ContentItem,
    content_type: ContentType,
    *,
    story_topic_counts: Dict[str, int],
    story_entity_counts: Dict[str, int],
    channel_config: ChannelConfig | None = None,
    source_profile: VideoSourceProfile | None = None,
) -> str | None:
    """Return a short reason when an item should not promote despite its score."""
    story_importance = compute_story_importance(item, story_topic_counts, story_entity_counts)
    role = _resolve_channel_role(channel_config, source_profile)
    content_format = _resolve_content_format(channel_config, source_profile)
    tech_score = compute_editorial_tech_score(item, channel_role=role)
    lane = _safe_text(getattr(item, "acquisition_lane", None))
    source_status = _safe_text(getattr(item, "source_status", None))
    title = getattr(item, "title", "") if isinstance(getattr(item, "title", None), str) else ""
    broad_news_source = _is_broad_news_source(item, channel_config, source_profile)

    if content_type == ContentType.REEL:
        if (
            broad_news_source
            and role == ChannelRole.NEWS
            and content_format == ContentFormat.LONG_FORM
        ):
            if any(pattern.search(title) for pattern in _BROAD_NEWS_LEAK_PATTERNS):
                return "off_topic_broad_news_reel"
            if story_importance < 0.28 and tech_score < 0.24:
                return "weak_broad_news_reel"
        if story_importance < 0.08 and tech_score < 0.32:
            return "weak_editorial_reel"
        if lane in {"search", "trending"} and source_status not in {"core", "rotation"}:
            if story_importance < 0.20:
                return "low_story_discovery_reel"
        if role == ChannelRole.OFFICIAL and story_importance < 0.12 and tech_score < 0.40:
            return "low_signal_official_reel"

    if (
        content_type == ContentType.VIDEO
        and role == ChannelRole.NEWS
        and content_format == ContentFormat.LONG_FORM
    ):
        if broad_news_source and any(
            pattern.search(title) for pattern in _BROAD_NEWS_LEAK_PATTERNS
        ):
            return "off_topic_broad_news_video"
        if any(pattern.search(title) for pattern in _VIDEO_LEAK_PATTERNS):
            return "off_topic_news_video"
        if broad_news_source and story_importance < 0.28 and tech_score < 0.22:
            return "weak_broad_news_video"
        if story_importance < 0.20 and tech_score < 0.38:
            return "weak_tech_signal_video"

    return None


# ── Per-item scoring ──────────────────────────────────────────────────────────


def score_candidate(
    item: ContentItem,
    cluster_sizes: Dict[str, int],
    config: PromotionConfig,
    *,
    promoted_topic_counts: Optional[Dict[str, int]] = None,
    promoted_channel_counts: Optional[Dict[str, int]] = None,
    story_topic_counts: Optional[Dict[str, int]] = None,
    story_entity_counts: Optional[Dict[str, int]] = None,
) -> float:
    """Compute the promotion score for a single ContentItem.

    Returns a value in roughly [-0.2, 1.0].  The hard floor in
    run_promotion_job() is config.min_score.
    """
    source_quality = compute_source_weight(item.source or "")
    source_status = _safe_text(getattr(item, "source_status", None))
    if source_status == "core":
        source_quality = min(source_quality * 1.05, 1.0)
    elif source_status == "rotation":
        source_quality *= 0.95
    elif source_status == "blocked":
        source_quality = 0.0
    elif source_status == "discovery":
        source_quality *= 0.8

    cluster_hotness = compute_cluster_hotness(
        item.cluster_id, item.signal_hits or 0, cluster_sizes, config.signal_hits_cap
    )
    recency = compute_promotion_recency(item.published_at, config.recency_half_life_hours)
    clickbait = compute_clickbait_penalty(item.title or "")
    duplicate_penalty = compute_duplicate_penalty(item.cluster_id, cluster_sizes)
    velocity = compute_velocity_score(getattr(item, "views_per_hour", None))
    format_fit = min(max(_safe_float(getattr(item, "format_fit_score", None), 0.5), 0.0), 1.0)
    topics = (
        getattr(item, "topics", None) if isinstance(getattr(item, "topics", None), list) else []
    )
    topic = topics[0] if topics else None
    category_gap_bonus = compute_category_gap_bonus(topic, promoted_topic_counts or {})
    channel_attr = getattr(item, "channel_id", None)
    channel_key = (
        channel_attr if isinstance(channel_attr, str) and channel_attr else item.source or ""
    )
    creator_fatigue = compute_creator_fatigue_penalty(
        (promoted_channel_counts or {}).get(channel_key, 0)
    )
    story_importance = compute_story_importance(
        item,
        story_topic_counts or {},
        story_entity_counts or {},
    )
    lane_penalty = 0.0
    lane = _safe_text(getattr(item, "acquisition_lane", None))
    if lane == "search" and source_status not in {"core", "rotation"}:
        lane_penalty = config.discovery_lane_penalty
    elif lane == "trending" and source_status not in {"core", "rotation"}:
        lane_penalty = config.discovery_lane_penalty * 0.25

    score = (
        config.w_source * source_quality
        + config.w_cluster * cluster_hotness
        + config.w_recency * recency
        + config.w_velocity * velocity
        + config.w_format_fit * format_fit
        + config.w_category_gap * category_gap_bonus
        + config.w_story * story_importance
        - config.w_clickbait * clickbait
        - config.w_duplicate * duplicate_penalty
        - config.w_creator_fatigue * creator_fatigue
        - lane_penalty
    )
    return round(score, 4)


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class PromotionResult:
    """Stats from a single promotion run."""

    candidates_evaluated: int = 0
    promoted_count: int = 0
    promoted_ids: List[int] = field(default_factory=list)
    already_promoted_rescored: int = 0
    errors: List[str] = field(default_factory=list)


# ── Main service ──────────────────────────────────────────────────────────────


class PromotionService:
    """Orchestrates the CANDIDATE→PROMOTED promotion pipeline.

    Usage::

        svc = PromotionService(db)
        result = svc.run_promotion_job()
    """

    def __init__(self, db: Session, config: PromotionConfig = _DEFAULT_CONFIG) -> None:
        self.db = db
        self.config = config

    # ── Cluster catalogue ─────────────────────────────────────────────────

    def _get_cluster_sizes(self, hours_back: int) -> Dict[str, int]:
        """Return a mapping cluster_id → item count for recent items."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        rows = (
            apply_content_policy(self.db.query(ContentItem.cluster_id, func.count(ContentItem.id)))
            .filter(
                ContentItem.cluster_id.isnot(None),
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
            )
            .group_by(ContentItem.cluster_id)
            .all()
        )
        return {row[0]: row[1] for row in rows if row[0]}

    # ── Candidate fetch ───────────────────────────────────────────────────

    def _get_candidates(self, content_type: ContentType) -> List[ContentItem]:
        cutoff = datetime.utcnow() - timedelta(
            hours=self._config_for_type(content_type).window_hours
        )
        return (
            apply_content_policy(
                self.db.query(ContentItem),
                content_type=content_type,
            )
            .filter(
                ContentItem.type == content_type,
                ContentItem.curation_status == ContentStatus.CANDIDATE,
                ContentItem.published_at >= cutoff,
                ContentItem.is_suppressed.is_(False),
            )
            .order_by(ContentItem.published_at.desc())
            .all()
        )

    def _get_recent_promoted_topic_counts(self, content_type: ContentType) -> Dict[str, int]:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        items = (
            apply_content_policy(
                self.db.query(ContentItem),
                content_type=content_type,
            )
            .filter(
                ContentItem.type == content_type,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
                ContentItem.published_at >= cutoff,
            )
            .all()
        )
        counts: Dict[str, int] = {}
        for item in items:
            topics = item.topics or []
            if topics:
                counts[topics[0]] = counts.get(topics[0], 0) + 1
        return counts

    def _get_recent_promoted_channel_counts(self, content_type: ContentType) -> Dict[str, int]:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        rows = (
            apply_content_policy(
                self.db.query(
                    func.coalesce(ContentItem.channel_id, ContentItem.source).label("channel_key"),
                    func.count(ContentItem.id),
                ),
                content_type=content_type,
            )
            .filter(
                ContentItem.type == content_type,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
                ContentItem.published_at >= cutoff,
            )
            .group_by("channel_key")
            .all()
        )
        return {row[0]: row[1] for row in rows if row[0]}

    def _get_recent_story_context(self) -> Tuple[Dict[str, int], Dict[str, int]]:
        """Build a 7-day cross-surface story graph used for video/reel ranking."""
        cutoff = datetime.utcnow() - timedelta(days=7)
        items = (
            apply_content_policy(self.db.query(ContentItem))
            .filter(
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
                ContentItem.published_at >= cutoff,
            )
            .all()
        )

        topic_counts: Dict[str, int] = {}
        entity_counts: Dict[str, int] = {}
        for item in items:
            weight = 2 if item.type == ContentType.ARTICLE else 1
            for topic in _normalize_metadata_terms(item.topics)[:2]:
                topic_counts[topic] = topic_counts.get(topic, 0) + weight
            for entity in _normalize_metadata_terms(item.entities)[:3]:
                entity_counts[entity] = entity_counts.get(entity, 0) + weight

        return topic_counts, entity_counts

    def _config_for_type(self, content_type: ContentType) -> PromotionConfig:
        if self.config is not _DEFAULT_CONFIG:
            return self.config
        if content_type == ContentType.VIDEO:
            return _VIDEO_CONFIG
        if content_type == ContentType.REEL:
            return _REEL_CONFIG
        return self.config

    def _get_source_profiles(self, items: List[ContentItem]) -> Dict[str, VideoSourceProfile]:
        channel_ids = [
            channel_id
            for channel_id in (getattr(item, "channel_id", None) for item in items)
            if isinstance(channel_id, str) and channel_id
        ]
        if not channel_ids:
            return {}
        repo = VideoSourceProfileRepository(self.db)
        return repo.get_many(channel_ids)

    def _channel_key_for_item(self, item: ContentItem) -> str:
        channel_id = getattr(item, "channel_id", None)
        if isinstance(channel_id, str) and channel_id:
            return channel_id
        source = getattr(item, "source", None)
        return source if isinstance(source, str) else ""

    def _surface_channel_cap(
        self,
        content_type: ContentType,
        *,
        item: ContentItem,
        channel_config: ChannelConfig | None,
        source_profile: VideoSourceProfile | None,
    ) -> int | None:
        if content_type != ContentType.REEL:
            return None
        if source_profile is not None:
            return max(0, int(source_profile.daily_reel_cap or 0))
        if channel_config is not None:
            return channel_config.effective_daily_reel_cap
        return 1

    def _has_editorial_override(self, item: ContentItem) -> bool:
        """Manual editorial actions should win over automatic rescoring demotions."""
        if getattr(item, "manual_added", False) is True:
            return True
        actor = getattr(item, "last_modified_by", None)
        return isinstance(actor, str) and bool(actor.strip())

    # ── Re-score existing PROMOTED items ──────────────────────────────────

    def _rescore_promoted(
        self,
        content_type: ContentType,
        cluster_sizes: Dict[str, int],
        config: PromotionConfig,
        promoted_topic_counts: Optional[Dict[str, int]] = None,
        promoted_channel_counts: Optional[Dict[str, int]] = None,
        story_topic_counts: Optional[Dict[str, int]] = None,
        story_entity_counts: Optional[Dict[str, int]] = None,
    ) -> int:
        """Refresh promotion_score on PROMOTED items (no status change)."""
        cutoff = datetime.utcnow() - timedelta(hours=config.window_hours)
        promoted_items = (
            apply_content_policy(
                self.db.query(ContentItem),
                content_type=content_type,
            )
            .filter(
                ContentItem.type == content_type,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff,
            )
            .all()
        )
        source_profiles = self._get_source_profiles(promoted_items)
        count = 0
        demote_reasons = {
            "off_topic_news_video",
            "off_topic_broad_news_video",
            "weak_broad_news_video",
            "weak_tech_signal_video",
            "off_topic_broad_news_reel",
            "weak_broad_news_reel",
        }
        for item in promoted_items:
            item.promotion_score = score_candidate(
                item,
                cluster_sizes,
                config,
                promoted_topic_counts=promoted_topic_counts,
                promoted_channel_counts=promoted_channel_counts,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            )
            item.promotion_reason = self._promotion_reason(
                item,
                config,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            )
            source_profile = source_profiles.get(getattr(item, "channel_id", None) or "")
            channel_config = _channel_config_for_item(item)
            block_reason = classify_promotion_block(
                item,
                content_type,
                story_topic_counts=story_topic_counts or {},
                story_entity_counts=story_entity_counts or {},
                channel_config=channel_config,
                source_profile=source_profile,
            )
            if block_reason:
                item.promotion_reason = f"{item.promotion_reason}|blocked={block_reason}"
            if block_reason in demote_reasons:
                if self._has_editorial_override(item):
                    item.promotion_reason = f"{item.promotion_reason}|preserved=editorial_override"
                else:
                    item.curation_status = ContentStatus.CANDIDATE
            sync_content_readiness(self.db, item)
            count += 1
        return count

    # ── Main run ──────────────────────────────────────────────────────────

    def run_promotion_job(self) -> PromotionResult:
        """Score all CANDIDATE items and promote the best ones.

        Runs independently per content type (ARTICLE, VIDEO, REEL) so that
        each surface has its own top-N allocation.
        """
        result = PromotionResult()

        try:
            max_window_hours = max(
                self.config.window_hours,
                _VIDEO_CONFIG.window_hours,
                _REEL_CONFIG.window_hours,
            )
            cluster_sizes = self._get_cluster_sizes(hours_back=max_window_hours * 2)
            story_topic_counts, story_entity_counts = self._get_recent_story_context()

            for content_type in (ContentType.ARTICLE, ContentType.VIDEO, ContentType.REEL):
                try:
                    promoted, evaluated, rescored, promoted_ids = self._promote_type(
                        content_type,
                        cluster_sizes,
                        story_topic_counts=story_topic_counts,
                        story_entity_counts=story_entity_counts,
                    )
                    result.promoted_count += promoted
                    result.promoted_ids.extend(promoted_ids)
                    result.candidates_evaluated += evaluated
                    result.already_promoted_rescored += rescored
                except Exception as exc:
                    msg = f"{content_type.value} promotion failed: {exc}"
                    logger.error("[promotion] %s", msg)
                    result.errors.append(msg)

            self.db.commit()
            if isinstance(self.db, Session):
                from app.services.video_source_service import refresh_video_source_health

                try:
                    refresh_video_source_health(self.db)
                except Exception as exc:
                    logger.warning("[promotion] source health refresh failed: %s", exc)

        except Exception as exc:
            self.db.rollback()
            msg = f"Promotion run failed: {exc}"
            logger.error("[promotion] %s", msg)
            result.errors.append(msg)

        logger.info(
            "[promotion] Run complete: evaluated=%d promoted=%d rescored=%d errors=%d",
            result.candidates_evaluated,
            result.promoted_count,
            result.already_promoted_rescored,
            len(result.errors),
        )
        return result

    def _promote_type(
        self,
        content_type: ContentType,
        cluster_sizes: Dict[str, int],
        *,
        story_topic_counts: Optional[Dict[str, int]] = None,
        story_entity_counts: Optional[Dict[str, int]] = None,
    ) -> Tuple[int, int, int, List[int]]:
        """Score and promote CANDIDATEs for a single content type.

        Returns (promoted_count, evaluated_count, rescored_promoted_count, promoted_ids).
        """
        candidates = self._get_candidates(content_type)
        evaluated = len(candidates)
        config = self._config_for_type(content_type)
        promoted_topic_counts = self._get_recent_promoted_topic_counts(content_type)
        promoted_channel_counts = self._get_recent_promoted_channel_counts(content_type)
        source_profiles = self._get_source_profiles(candidates)

        # Score every candidate
        scored: List[
            Tuple[
                float,
                ContentItem,
                str | None,
                ChannelConfig | None,
                VideoSourceProfile | None,
            ]
        ] = []
        for item in candidates:
            source_profile = source_profiles.get(getattr(item, "channel_id", None) or "")
            channel_config = _channel_config_for_item(item)
            s = score_candidate(
                item,
                cluster_sizes,
                config,
                promoted_topic_counts=promoted_topic_counts,
                promoted_channel_counts=promoted_channel_counts,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            )
            block_reason = classify_promotion_block(
                item,
                content_type,
                story_topic_counts=story_topic_counts or {},
                story_entity_counts=story_entity_counts or {},
                channel_config=channel_config,
                source_profile=source_profile,
            )
            item.promotion_score = s
            item.promotion_reason = self._promotion_reason(
                item,
                config,
                story_topic_counts=story_topic_counts,
                story_entity_counts=story_entity_counts,
            )
            if block_reason:
                item.promotion_reason = f"{item.promotion_reason}|blocked={block_reason}"
            scored.append((s, item, block_reason, channel_config, source_profile))

        # Sort descending by promotion score
        scored.sort(key=lambda t: t[0], reverse=True)

        promoted = 0
        promoted_ids: List[int] = []
        for s, item, block_reason, channel_config, source_profile in scored:
            if promoted >= config.top_n_per_type:
                break
            if s < config.min_score:
                break
            if block_reason:
                continue
            channel_key = self._channel_key_for_item(item)
            cap = self._surface_channel_cap(
                content_type,
                item=item,
                channel_config=channel_config,
                source_profile=source_profile,
            )
            if cap is not None:
                if cap <= 0:
                    item.promotion_reason = f"{item.promotion_reason}|blocked=reel_cap_zero"
                    continue
                if promoted_channel_counts.get(channel_key, 0) >= cap:
                    item.promotion_reason = f"{item.promotion_reason}|blocked=daily_reel_cap"
                    continue
            item.curation_status = ContentStatus.PROMOTED
            sync_content_readiness(self.db, item)
            promoted += 1
            promoted_ids.append(int(item.id))
            promoted_channel_counts[channel_key] = promoted_channel_counts.get(channel_key, 0) + 1

        rescored = self._rescore_promoted(
            content_type,
            cluster_sizes,
            config,
            promoted_topic_counts=promoted_topic_counts,
            promoted_channel_counts=promoted_channel_counts,
            story_topic_counts=story_topic_counts,
            story_entity_counts=story_entity_counts,
        )

        logger.info(
            "[promotion] %s: evaluated=%d promoted=%d (threshold=%.2f)",
            content_type.value,
            evaluated,
            promoted,
            config.min_score,
        )
        return promoted, evaluated, rescored, promoted_ids

    def _promotion_reason(
        self,
        item: ContentItem,
        config: PromotionConfig,
        *,
        story_topic_counts: Optional[Dict[str, int]] = None,
        story_entity_counts: Optional[Dict[str, int]] = None,
    ) -> str:
        """Compact explanation persisted for admin diagnostics."""
        lane = (
            getattr(item, "acquisition_lane", None)
            if isinstance(getattr(item, "acquisition_lane", None), str)
            else "curated"
        )
        source_status = (
            getattr(item, "source_status", None)
            if isinstance(getattr(item, "source_status", None), str)
            else "unknown"
        )
        return (
            f"{lane}|{source_status}|"
            f"fit={_safe_float(getattr(item, 'format_fit_score', None), 0.0):.2f}|"
            f"vph={_safe_float(getattr(item, 'views_per_hour', None), 0.0):.1f}|"
            f"story={compute_story_importance(item, story_topic_counts or {}, story_entity_counts or {}):.2f}"
        )
