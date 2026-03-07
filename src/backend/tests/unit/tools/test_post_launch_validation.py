"""Unit tests for rollout validation helper script."""

from scripts.post_launch_validation import (
    build_checks,
    evaluate_inventory_health,
    evaluate_inventory_metrics,
)


def test_build_checks_includes_core_endpoints():
    checks = build_checks()
    names = {check.name for check in checks}

    assert "health" in names
    assert "inventory_health" in names
    assert "session_playlist" in names
    assert "source_metrics" in names
    assert "inventory_metrics" in names


def test_evaluate_inventory_health_flags_unhealthy_and_topup():
    issues = evaluate_inventory_health({"is_healthy": False, "needs_topup": True})
    assert "inventory reports unhealthy state" in issues
    assert "inventory requests top-up" in issues


def test_evaluate_inventory_metrics_flags_guardrail_breaches():
    issues = evaluate_inventory_metrics({"dominant_source_pct": 42.0, "infra_share_pct": 4.0})
    assert any("dominant source too high" in issue for issue in issues)
    assert any("infra coverage too low" in issue for issue in issues)


def test_evaluate_inventory_metrics_allows_healthy_mix():
    issues = evaluate_inventory_metrics({"dominant_source_pct": 21.5, "infra_share_pct": 18.0})
    assert issues == []
