from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.playlist_service import PlaylistService


class _RankingStub:
    def __init__(self):
        self.calls = []

    def score_item(self, item, *, personalization_score: float = 0.0) -> float:
        self.calls.append((item.id, personalization_score))
        return float(item.id) + float(personalization_score)


def _item(id_: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        source="techcrunch",
        global_score=0.5,
        promotion_score=0.5,
        editorial_boost=0,
        topics=["AI"],
        cluster_id=f"c{id_}",
    )


def test_score_candidates_uses_ranking_service_and_sorts_descending():
    ranking = _RankingStub()
    personalization = MagicMock()
    personalization.compute_personalization_score.side_effect = [0.1, 0.2]

    service = PlaylistService(
        content_repo=MagicMock(),
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        ranking_service=ranking,
        redis_client=None,
    )

    scored = service._score_candidates("device-1", [_item(1), _item(2)])

    assert ranking.calls == [(1, 0.1), (2, 0.2)]
    assert [item.id for item, _ in scored] == [2, 1]
