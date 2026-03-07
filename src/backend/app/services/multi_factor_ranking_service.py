"""Multi-factor ranking service for playlist ordering."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.ranking.quality import compute_source_weight


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_timestamp(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FreshnessConfig:
    """Controls freshness boost/decay behaviour."""

    half_life_hours: float = 18.0
    boost_window_hours: float = 4.0
    boost_max: float = 0.20
    max_age_hours: float = 96.0


@dataclass(frozen=True)
class RankingWeights:
    """Weights used to combine ranking signals."""

    global_score: float = 0.34
    promotion_score: float = 0.18
    personalization: float = 0.18
    source_quality: float = 0.10
    editorial: float = 0.10
    freshness: float = 0.10

    def normalized(self) -> "RankingWeights":
        total = (
            self.global_score
            + self.promotion_score
            + self.personalization
            + self.source_quality
            + self.editorial
            + self.freshness
        )
        if total <= 0:
            return RankingWeights()
        return RankingWeights(
            global_score=self.global_score / total,
            promotion_score=self.promotion_score / total,
            personalization=self.personalization / total,
            source_quality=self.source_quality / total,
            editorial=self.editorial / total,
            freshness=self.freshness / total,
        )


@dataclass(frozen=True)
class RankingBreakdown:
    final_score: float
    components: Dict[str, float]


class MultiFactorRankingService:
    """Ranks content with freshness boost + decay baked into final score."""

    def __init__(
        self,
        weights: Optional[RankingWeights] = None,
        freshness: Optional[FreshnessConfig] = None,
    ) -> None:
        self.weights = (weights or RankingWeights()).normalized()
        self.freshness = freshness or FreshnessConfig()

    def _content_age_hours(self, item: Any, now: datetime) -> Optional[float]:
        published = _normalize_timestamp(getattr(item, "published_at", None))
        created = _normalize_timestamp(getattr(item, "created_at", None))
        timestamp = published or created
        if timestamp is None:
            return None
        age_hours = max(0.0, (now - timestamp).total_seconds() / 3600.0)
        return age_hours

    def compute_freshness_score(
        self,
        item: Any,
        *,
        now: Optional[datetime] = None,
    ) -> float:
        """Compute freshness from age using boost-then-decay curve."""
        now_utc = _normalize_timestamp(now or datetime.now(timezone.utc))
        if now_utc is None:
            return 0.0

        age_hours = self._content_age_hours(item, now_utc)
        if age_hours is None:
            return 0.0
        if age_hours >= self.freshness.max_age_hours:
            return 0.0

        # Exponential decay around half-life.
        decay = math.exp((-math.log(2.0) * age_hours) / max(0.1, self.freshness.half_life_hours))

        # Boost only very fresh items, then taper to 0 at boost_window.
        boost = 0.0
        if age_hours <= self.freshness.boost_window_hours:
            freshness_ratio = 1.0 - (age_hours / max(0.1, self.freshness.boost_window_hours))
            boost = self.freshness.boost_max * freshness_ratio

        return _clamp(decay + boost, 0.0, 1.0)

    def explain_item(
        self,
        item: Any,
        *,
        personalization_score: float = 0.0,
        now: Optional[datetime] = None,
    ) -> RankingBreakdown:
        """Compute score plus per-component contributions."""
        global_component = _clamp(float(getattr(item, "global_score", 0.0) or 0.0), 0.0, 1.5)

        promotion_raw = getattr(item, "promotion_score", None)
        if promotion_raw is None:
            promotion_component = _clamp(global_component, 0.0, 1.0)
        else:
            promotion_component = _clamp(float(promotion_raw), 0.0, 1.0)

        personalization_component = _clamp(float(personalization_score or 0.0), 0.0, 1.0)
        source_component = _clamp(compute_source_weight(getattr(item, "source", "") or ""), 0.0, 1.0)
        editorial_level = _clamp(float(getattr(item, "editorial_boost", 0) or 0), 0.0, 3.0)
        editorial_component = editorial_level / 3.0
        freshness_component = self.compute_freshness_score(item, now=now)

        weighted = {
            "global_score": round(self.weights.global_score * global_component, 6),
            "promotion_score": round(self.weights.promotion_score * promotion_component, 6),
            "personalization": round(self.weights.personalization * personalization_component, 6),
            "source_quality": round(self.weights.source_quality * source_component, 6),
            "editorial": round(self.weights.editorial * editorial_component, 6),
            "freshness": round(self.weights.freshness * freshness_component, 6),
        }
        final_score = round(sum(weighted.values()), 6)
        return RankingBreakdown(final_score=final_score, components=weighted)

    def score_item(
        self,
        item: Any,
        *,
        personalization_score: float = 0.0,
        now: Optional[datetime] = None,
    ) -> float:
        """Compute a single ranking score."""
        return self.explain_item(
            item,
            personalization_score=personalization_score,
            now=now,
        ).final_score

    def rank_items(
        self,
        items: Iterable[Any],
        *,
        personalization_scores: Optional[Dict[int, float]] = None,
        now: Optional[datetime] = None,
    ) -> List[Tuple[Any, float]]:
        """Return items sorted by descending score."""
        scores = personalization_scores or {}
        ranked: List[Tuple[Any, float]] = []
        for item in items:
            item_id = int(getattr(item, "id", 0) or 0)
            score = self.score_item(
                item,
                personalization_score=float(scores.get(item_id, 0.0)),
                now=now,
            )
            ranked.append((item, score))
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked
