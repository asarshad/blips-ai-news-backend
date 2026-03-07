from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.multi_factor_ranking_service import MultiFactorRankingService


def _item(
    *,
    id_: int,
    source: str,
    global_score: float,
    promotion_score: float | None = None,
    editorial_boost: int = 0,
    published_at: datetime | None = None,
):
    return SimpleNamespace(
        id=id_,
        source=source,
        global_score=global_score,
        promotion_score=promotion_score,
        editorial_boost=editorial_boost,
        published_at=published_at,
    )


def test_high_promotion_and_editorial_can_outrank_higher_base_score():
    service = MultiFactorRankingService()
    baseline = _item(
        id_=1,
        source="unknown",
        global_score=0.85,
        promotion_score=0.20,
        editorial_boost=0,
        published_at=datetime.now(timezone.utc) - timedelta(hours=12),
    )
    candidate = _item(
        id_=2,
        source="ars technica",
        global_score=0.72,
        promotion_score=0.95,
        editorial_boost=2,
        published_at=datetime.now(timezone.utc) - timedelta(hours=12),
    )

    baseline_score = service.score_item(baseline, personalization_score=0.0)
    candidate_score = service.score_item(candidate, personalization_score=0.0)

    assert candidate_score > baseline_score


def test_freshness_boost_beats_stale_when_other_signals_equal():
    service = MultiFactorRankingService()
    now = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)

    fresh = _item(
        id_=1,
        source="techcrunch",
        global_score=0.70,
        promotion_score=0.60,
        editorial_boost=0,
        published_at=now - timedelta(minutes=20),
    )
    stale = _item(
        id_=2,
        source="techcrunch",
        global_score=0.70,
        promotion_score=0.60,
        editorial_boost=0,
        published_at=now - timedelta(hours=48),
    )

    fresh_score = service.score_item(fresh, personalization_score=0.2, now=now)
    stale_score = service.score_item(stale, personalization_score=0.2, now=now)

    assert fresh_score > stale_score


def test_freshness_decay_component_reaches_zero_past_max_age():
    service = MultiFactorRankingService()
    now = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)

    very_old = _item(
        id_=1,
        source="wired",
        global_score=0.6,
        promotion_score=0.6,
        published_at=now - timedelta(hours=200),
    )

    breakdown = service.explain_item(very_old, now=now)
    assert breakdown.components["freshness"] == 0.0


def test_rank_items_orders_descending_with_freshness_influence():
    service = MultiFactorRankingService()
    now = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)
    items = [
        _item(
            id_=1,
            source="unknown",
            global_score=0.50,
            promotion_score=0.20,
            editorial_boost=0,
            published_at=now - timedelta(hours=72),
        ),
        _item(
            id_=2,
            source="ars technica",
            global_score=0.65,
            promotion_score=0.90,
            editorial_boost=1,
            published_at=now - timedelta(hours=24),
        ),
        _item(
            id_=3,
            source="techcrunch",
            global_score=0.60,
            promotion_score=0.40,
            editorial_boost=0,
            published_at=now - timedelta(minutes=45),
        ),
    ]

    ranked = service.rank_items(items, personalization_scores={1: 0.0, 2: 0.2, 3: 0.1}, now=now)
    ranked_ids = [item.id for item, _ in ranked]

    assert ranked_ids == [2, 3, 1]
