"""Source quality scoring and promotion/demotion rules.

This service turns per-source ingestion outcomes into a stable quality score and
applies bounded weight adjustments to source governance records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Literal, Optional

from sqlalchemy.orm import Session

from app.models.source import Source, SourceDailyStat

Action = Literal["promote", "demote", "hold"]


@dataclass(frozen=True)
class SourceQualityComponents:
    success_rate: float
    volume_score: float
    extraction_health: float


@dataclass(frozen=True)
class SourceQualityDecision:
    source: str
    quality_score: float
    action: Action
    old_weight: float
    new_weight: float
    components: SourceQualityComponents


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_source_quality_score(
    *,
    inserted: int,
    suppressed: int,
    extraction_health: Optional[float] = None,
    volume_target: int = 12,
) -> tuple[float, SourceQualityComponents]:
    """Compute source quality from outcomes and extraction reliability.

    Weighted score:
    - success_rate      55%
    - volume_score      25%
    - extraction_health 20%
    """
    inserted_i = max(0, int(inserted or 0))
    suppressed_i = max(0, int(suppressed or 0))

    total = inserted_i + suppressed_i
    success_rate = (inserted_i / total) if total > 0 else 0.5

    target = max(1, int(volume_target))
    volume_score = _clamp(inserted_i / target, 0.0, 1.0)

    extraction = 0.60 if extraction_health is None else _clamp(float(extraction_health), 0.0, 1.0)

    score = (0.55 * success_rate) + (0.25 * volume_score) + (0.20 * extraction)
    score = _clamp(score, 0.0, 1.0)

    return score, SourceQualityComponents(
        success_rate=round(success_rate, 4),
        volume_score=round(volume_score, 4),
        extraction_health=round(extraction, 4),
    )


def decide_weight_adjustment(
    *,
    current_weight: float,
    quality_score: float,
    promotion_threshold: float = 0.75,
    demotion_threshold: float = 0.45,
    step: float = 0.05,
    min_weight: float = 0.40,
    max_weight: float = 1.20,
) -> tuple[Action, float]:
    """Decide bounded source-weight adjustment from quality score."""
    weight = _clamp(float(current_weight), min_weight, max_weight)
    step_size = max(0.0, float(step))

    if quality_score >= promotion_threshold:
        updated = _clamp(weight + step_size, min_weight, max_weight)
        if updated > weight:
            return "promote", round(updated, 4)
        return "hold", round(weight, 4)

    if quality_score <= demotion_threshold:
        updated = _clamp(weight - step_size, min_weight, max_weight)
        if updated < weight:
            return "demote", round(updated, 4)
        return "hold", round(weight, 4)

    return "hold", round(weight, 4)


class SourceQualityService:
    """Apply source quality scoring and promotion/demotion weight updates."""

    def __init__(
        self,
        db: Session,
        *,
        source_health_provider: Optional[Callable[[], Dict[str, Dict[str, float]]]] = None,
    ) -> None:
        self.db = db
        self._source_health_provider = source_health_provider

    def _load_source_health(self) -> Dict[str, Dict[str, float]]:
        if self._source_health_provider is None:
            return {}
        try:
            return self._source_health_provider() or {}
        except Exception:
            return {}

    def _iter_recent_stats(self, *, source_name: str, day_utc: date, window_days: int) -> Iterable[SourceDailyStat]:
        since = day_utc - timedelta(days=max(1, window_days) - 1)
        return (
            self.db.query(SourceDailyStat)
            .filter(
                SourceDailyStat.source == source_name,
                SourceDailyStat.day >= since,
                SourceDailyStat.day <= day_utc,
            )
            .all()
        )

    def rebalance_source_weights(
        self,
        *,
        day_utc: Optional[date] = None,
        window_days: int = 7,
        volume_target: int = 12,
    ) -> List[SourceQualityDecision]:
        """Recompute and apply source weight adjustments for active sources."""
        target_day = day_utc or date.today()
        health_map = {
            (k or "").strip().lower(): (v or {}) for k, v in self._load_source_health().items()
        }

        sources = self.db.query(Source).filter(Source.enabled.is_(True)).all()
        decisions: List[SourceQualityDecision] = []

        for source in sources:
            stats = list(
                self._iter_recent_stats(
                    source_name=source.name,
                    day_utc=target_day,
                    window_days=window_days,
                )
            )
            inserted = sum(int(s.inserted or 0) for s in stats)
            suppressed = sum(int(s.suppressed or 0) for s in stats)

            health_payload = health_map.get(source.name.strip().lower(), {})
            extraction_health = health_payload.get("health_score")

            quality_score, components = compute_source_quality_score(
                inserted=inserted,
                suppressed=suppressed,
                extraction_health=extraction_health,
                volume_target=volume_target,
            )

            action, new_weight = decide_weight_adjustment(
                current_weight=float(source.weight or 1.0),
                quality_score=quality_score,
            )

            old_weight = round(float(source.weight or 1.0), 4)
            if new_weight != old_weight:
                source.weight = new_weight

            decisions.append(
                SourceQualityDecision(
                    source=source.name,
                    quality_score=round(quality_score, 4),
                    action=action,
                    old_weight=old_weight,
                    new_weight=new_weight,
                    components=components,
                )
            )

        self.db.commit()
        return decisions
