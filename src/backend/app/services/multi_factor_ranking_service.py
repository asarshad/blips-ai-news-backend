"""Multi-factor ranking service for playlist ordering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.ranking.quality import compute_source_weight


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class RankingWeights:
    """Weights used to combine ranking signals."""

    global_score: float = 0.40
    promotion_score: float = 0.20
    personalization: float = 0.20
    source_quality: float = 0.10
    editorial: float = 0.10

    def normalized(self) -> "RankingWeights":
        total = (
            self.global_score
            + self.promotion_score
            + self.personalization
            + self.source_quality
            + self.editorial
        )
        if total <= 0:
            return RankingWeights()
        return RankingWeights(
            global_score=self.global_score / total,
            promotion_score=self.promotion_score / total,
            personalization=self.personalization / total,
            source_quality=self.source_quality / total,
            editorial=self.editorial / total,
        )


@dataclass(frozen=True)
class RankingBreakdown:
    final_score: float
    components: Dict[str, float]


class MultiFactorRankingService:
    """Ranks content using global score, promotion, source quality, and editorial context."""

    def __init__(self, weights: Optional[RankingWeights] = None) -> None:
        self.weights = (weights or RankingWeights()).normalized()

    def explain_item(self, item: Any, *, personalization_score: float = 0.0) -> RankingBreakdown:
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

        weighted = {
            "global_score": round(self.weights.global_score * global_component, 6),
            "promotion_score": round(self.weights.promotion_score * promotion_component, 6),
            "personalization": round(self.weights.personalization * personalization_component, 6),
            "source_quality": round(self.weights.source_quality * source_component, 6),
            "editorial": round(self.weights.editorial * editorial_component, 6),
        }
        final_score = round(sum(weighted.values()), 6)
        return RankingBreakdown(final_score=final_score, components=weighted)

    def score_item(self, item: Any, *, personalization_score: float = 0.0) -> float:
        """Compute a single ranking score."""
        return self.explain_item(item, personalization_score=personalization_score).final_score

    def rank_items(
        self,
        items: Iterable[Any],
        *,
        personalization_scores: Optional[Dict[int, float]] = None,
    ) -> List[Tuple[Any, float]]:
        """Return items sorted by descending score."""
        scores = personalization_scores or {}
        ranked: List[Tuple[Any, float]] = []
        for item in items:
            item_id = int(getattr(item, "id", 0) or 0)
            score = self.score_item(item, personalization_score=float(scores.get(item_id, 0.0)))
            ranked.append((item, score))
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked
