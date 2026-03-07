from datetime import date

from app.models.source import Source, SourceDailyStat
from app.services.source_quality_service import (
    SourceQualityService,
    compute_source_quality_score,
    decide_weight_adjustment,
)


class _QueryStub:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, sources, stats):
        self._sources = sources
        self._stats = stats
        self.committed = False

    def query(self, model):
        if model is Source:
            return _QueryStub(self._sources)
        if model is SourceDailyStat:
            return _QueryStub(self._stats)
        raise AssertionError(f"Unexpected query model: {model}")

    def commit(self):
        self.committed = True


def test_compute_source_quality_score_balances_components():
    score, components = compute_source_quality_score(
        inserted=9,
        suppressed=1,
        extraction_health=0.8,
        volume_target=10,
    )

    assert 0.0 <= score <= 1.0
    assert components.success_rate == 0.9
    assert components.volume_score == 0.9
    assert components.extraction_health == 0.8


def test_decide_weight_adjustment_promotes_and_demotes_with_bounds():
    action_up, up = decide_weight_adjustment(current_weight=1.0, quality_score=0.90)
    action_down, down = decide_weight_adjustment(current_weight=1.0, quality_score=0.20)

    assert action_up == "promote"
    assert up > 1.0

    assert action_down == "demote"
    assert down < 1.0


def test_rebalance_source_weights_promotes_high_quality_source():
    source = Source(name="TechCrunch", weight=1.0, enabled=True)
    stats = [
        SourceDailyStat(day=date(2026, 3, 6), source="TechCrunch", inserted=10, suppressed=1),
        SourceDailyStat(day=date(2026, 3, 5), source="TechCrunch", inserted=8, suppressed=1),
    ]
    db = _FakeSession([source], stats)

    service = SourceQualityService(
        db,
        source_health_provider=lambda: {"techcrunch": {"health_score": 0.92}},
    )
    decisions = service.rebalance_source_weights(day_utc=date(2026, 3, 6), volume_target=10)

    assert db.committed is True
    assert len(decisions) == 1
    assert decisions[0].action == "promote"
    assert decisions[0].new_weight > decisions[0].old_weight
    assert source.weight == decisions[0].new_weight


def test_rebalance_source_weights_demotes_low_quality_source():
    source = Source(name="NoisySource", weight=1.0, enabled=True)
    stats = [
        SourceDailyStat(day=date(2026, 3, 6), source="NoisySource", inserted=1, suppressed=9),
        SourceDailyStat(day=date(2026, 3, 5), source="NoisySource", inserted=0, suppressed=6),
    ]
    db = _FakeSession([source], stats)

    service = SourceQualityService(
        db,
        source_health_provider=lambda: {"noisysource": {"health_score": 0.2}},
    )
    decisions = service.rebalance_source_weights(day_utc=date(2026, 3, 6), volume_target=10)

    assert decisions[0].action == "demote"
    assert decisions[0].new_weight < decisions[0].old_weight
