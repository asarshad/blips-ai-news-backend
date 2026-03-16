"""Tests for auto-graduation logic in video_source_service."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.video_source import VideoSourceProfile
from app.services.video_source_service import _compute_graduated_status


def _make_profile(**overrides) -> VideoSourceProfile:
    defaults = dict(
        channel_id="test-channel",
        channel_name="Test Channel",
        role="explainer",
        content_format="mixed",
        quality_tier="premium",
        status="discovery",
        enabled=True,
        curated_seed=False,
        daily_video_cap=2,
        daily_reel_cap=4,
        allow_curated=True,
        allow_search=True,
        allow_trending=True,
        score_7d=0.5,
        promotion_rate_7d=0.0,
        suppression_rate_7d=0.0,
        clickbait_rate_7d=0.0,
        freshness_yield_7d=0.0,
        post_start_consumption_7d=0.0,
        promoted_share_7d=0.0,
        early_skip_rate_7d=0.0,
        completion_rate_7d=0.0,
        save_share_rate_7d=0.0,
        status_changed_at=None,
        probation_until=None,
    )
    defaults.update(overrides)
    return VideoSourceProfile(**defaults)


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_a, **_kw):
        return self

    def scalar(self):
        return self._value


class _FakeDb:
    def __init__(self, promoted_30d=0):
        self._promoted_30d = promoted_30d

    def query(self, *_a, **_kw):
        return _FakeQuery(self._promoted_30d)


def test_blocked_when_disabled():
    p = _make_profile(enabled=False, status="core")
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=10, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "blocked"
    )


def test_blocked_when_score_very_low():
    p = _make_profile(score_7d=0.20, status="rotation")
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "blocked"
    )


def test_blocked_when_suppression_high():
    p = _make_profile(score_7d=0.60, suppression_rate_7d=0.65, status="rotation")
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "blocked"
    )


def test_discovery_to_rotation_graduation():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.30,
        clickbait_rate_7d=0.05,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "rotation"
    )


def test_discovery_stays_when_promotion_rate_too_low():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.20,
        clickbait_rate_7d=0.05,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_discovery_stays_when_clickbait_too_high():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.30,
        clickbait_rate_7d=0.12,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_discovery_stays_when_promoted_count_too_low():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.30,
        clickbait_rate_7d=0.05,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=2, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_rotation_to_core_graduation():
    p = _make_profile(
        status="rotation",
        promotion_rate_7d=0.45,
        freshness_yield_7d=0.35,
        score_7d=0.75,
    )
    assert (
        _compute_graduated_status(
            profile=p,
            promoted_count_7d=5,
            db=_FakeDb(promoted_30d=12),
            cutoff_30d=datetime.utcnow() - timedelta(days=30),
        )
        == "core"
    )


def test_rotation_stays_when_30d_promoted_too_low():
    p = _make_profile(
        status="rotation",
        promotion_rate_7d=0.45,
        freshness_yield_7d=0.35,
        score_7d=0.75,
    )
    assert (
        _compute_graduated_status(
            profile=p,
            promoted_count_7d=5,
            db=_FakeDb(promoted_30d=8),
            cutoff_30d=datetime.utcnow() - timedelta(days=30),
        )
        == "rotation"
    )


def test_core_demoted_when_promotion_rate_low():
    p = _make_profile(
        status="core",
        promotion_rate_7d=0.05,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=1, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_rotation_demoted_when_promotion_rate_low():
    p = _make_profile(
        status="rotation",
        promotion_rate_7d=0.08,
        score_7d=0.55,
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=1, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_cooldown_prevents_graduation():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.30,
        clickbait_rate_7d=0.05,
        score_7d=0.55,
        status_changed_at=datetime.utcnow() - timedelta(days=3),
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_cooldown_prevents_demotion():
    p = _make_profile(
        status="core",
        promotion_rate_7d=0.05,
        score_7d=0.55,
        status_changed_at=datetime.utcnow() - timedelta(days=3),
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=1, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "core"
    )


def test_cooldown_expired_allows_graduation():
    p = _make_profile(
        status="discovery",
        promotion_rate_7d=0.30,
        clickbait_rate_7d=0.05,
        score_7d=0.55,
        status_changed_at=datetime.utcnow() - timedelta(days=8),
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=5, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "rotation"
    )


def test_probation_channel_demoted_on_low_rate():
    p = _make_profile(
        status="rotation",
        promotion_rate_7d=0.05,
        score_7d=0.55,
        probation_until=datetime.utcnow() + timedelta(days=7),
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=1, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "discovery"
    )


def test_hard_block_overrides_cooldown():
    """A channel should be blocked even during cooldown if metrics are terrible."""
    p = _make_profile(
        status="rotation",
        score_7d=0.15,
        status_changed_at=datetime.utcnow() - timedelta(days=1),
    )
    assert (
        _compute_graduated_status(
            profile=p, promoted_count_7d=0, db=_FakeDb(), cutoff_30d=datetime.utcnow()
        )
        == "blocked"
    )
