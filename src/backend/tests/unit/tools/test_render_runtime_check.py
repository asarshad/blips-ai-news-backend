from __future__ import annotations

from datetime import UTC, datetime

import scripts.render_runtime_check as render_runtime_check
from scripts.render_runtime_check import (
    build_alert_payload,
    select_alertable_anomalies,
    summarize_events,
)


def test_summarize_events_flags_oom_failures(monkeypatch):
    monkeypatch.setattr(
        render_runtime_check,
        "_utcnow",
        lambda: datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
    )
    summary = summarize_events(
        [
            {
                "event": {
                    "timestamp": "2026-04-01T03:00:00Z",
                    "type": "server_failed",
                    "details": {"reason": {"oomKilled": {"memoryLimit": "512Mi"}}},
                }
            }
        ],
        recent_hours=24,
    )

    assert len(summary["anomalies"]) == 1
    assert "oomKilled" in summary["anomalies"][0]["summary"]


def test_summarize_events_ignores_old_failures(monkeypatch):
    monkeypatch.setattr(
        render_runtime_check,
        "_utcnow",
        lambda: datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
    )
    summary = summarize_events(
        [
            {
                "event": {
                    "timestamp": "2026-03-25T03:00:00Z",
                    "type": "server_failed",
                    "details": {"reason": {"oomKilled": {"memoryLimit": "512Mi"}}},
                }
            }
        ],
        recent_hours=24,
    )

    assert summary["anomalies"] == []


def test_summarize_events_ignores_superseded_canceled_deploys(monkeypatch):
    monkeypatch.setattr(
        render_runtime_check,
        "_utcnow",
        lambda: datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
    )
    summary = summarize_events(
        [
            {
                "event": {
                    "timestamp": "2026-04-01T03:00:00Z",
                    "type": "deploy_ended",
                    "details": {
                        "deployStatus": "canceled",
                        "reason": {"newDeploy": {"id": "dep-next"}},
                    },
                }
            }
        ],
        recent_hours=24,
    )

    assert summary["anomalies"] == []


def test_summarize_events_handles_non_dict_reason(monkeypatch):
    monkeypatch.setattr(
        render_runtime_check,
        "_utcnow",
        lambda: datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
    )
    summary = summarize_events(
        [
            {
                "event": {
                    "timestamp": "2026-04-01T03:00:00Z",
                    "type": "deploy_ended",
                    "details": {
                        "deployStatus": "canceled",
                        "reason": "manual cancel",
                    },
                }
            }
        ],
        recent_hours=24,
    )

    assert len(summary["anomalies"]) == 1
    assert "canceled" in summary["anomalies"][0]["summary"]


def test_select_alertable_anomalies_filters_old_alerts():
    anomalies = [
        {"timestamp": "2026-04-01T03:30:00+00:00", "summary": "fresh"},
        {"timestamp": "2026-04-01T01:30:00+00:00", "summary": "old"},
    ]

    selected = select_alertable_anomalies(
        anomalies,
        max_age_minutes=70,
        now=datetime(2026, 4, 1, 4, 0, tzinfo=UTC),
    )

    assert selected == [{"timestamp": "2026-04-01T03:30:00+00:00", "summary": "fresh"}]


def test_build_alert_payload_mentions_service_and_anomalies():
    payload = build_alert_payload(
        service_id="srv-test",
        summary={
            "window_hours": 24,
            "anomalies": [
                {
                    "timestamp": "2026-04-01T03:00:00+00:00",
                    "summary": "oomKilled at limit 512Mi",
                }
            ],
        },
    )

    assert "srv-test" in payload["text"]
    assert "oomKilled" in payload["text"]
