from types import SimpleNamespace

from app.services.multi_factor_ranking_service import MultiFactorRankingService


def _item(
    *,
    id_: int,
    source: str,
    global_score: float,
    promotion_score: float | None = None,
    editorial_boost: int = 0,
):
    return SimpleNamespace(
        id=id_,
        source=source,
        global_score=global_score,
        promotion_score=promotion_score,
        editorial_boost=editorial_boost,
    )


def test_high_promotion_and_editorial_can_outrank_higher_base_score():
    service = MultiFactorRankingService()
    baseline = _item(id_=1, source="unknown", global_score=0.85, promotion_score=0.20, editorial_boost=0)
    candidate = _item(
        id_=2,
        source="ars technica",
        global_score=0.72,
        promotion_score=0.95,
        editorial_boost=2,
    )

    baseline_score = service.score_item(baseline, personalization_score=0.0)
    candidate_score = service.score_item(candidate, personalization_score=0.0)

    assert candidate_score > baseline_score


def test_personalization_boost_increases_score():
    service = MultiFactorRankingService()
    item = _item(id_=10, source="techcrunch", global_score=0.70, promotion_score=0.60, editorial_boost=0)

    without_personalization = service.score_item(item, personalization_score=0.0)
    with_personalization = service.score_item(item, personalization_score=0.9)

    assert with_personalization > without_personalization


def test_rank_items_orders_descending():
    service = MultiFactorRankingService()
    items = [
        _item(id_=1, source="unknown", global_score=0.50, promotion_score=0.20, editorial_boost=0),
        _item(
            id_=2,
            source="ars technica",
            global_score=0.65,
            promotion_score=0.90,
            editorial_boost=1,
        ),
        _item(id_=3, source="techcrunch", global_score=0.60, promotion_score=0.40, editorial_boost=0),
    ]

    ranked = service.rank_items(items, personalization_scores={1: 0.0, 2: 0.2, 3: 0.1})
    ranked_ids = [item.id for item, _ in ranked]

    assert ranked_ids == [2, 3, 1]
