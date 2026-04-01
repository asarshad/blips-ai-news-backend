from __future__ import annotations

from types import SimpleNamespace

from app.scheduler.tasks_signals import run_signal_ingestion_job


def test_run_signal_ingestion_job_alerts_on_deadlock(monkeypatch):
    alerts: list[dict[str, object]] = []

    class _FakeDB:
        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr("app.scheduler.tasks_signals.feature_flags.is_enabled", lambda name: True)
    monkeypatch.setattr("app.scheduler.tasks_signals.SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        "app.scheduler.tasks_signals.log_job_start",
        lambda name: SimpleNamespace(
            errors=[],
            items_processed=0,
            complete=lambda: None,
            log_summary=lambda: None,
        ),
    )
    monkeypatch.setattr(
        "app.ingestion.signal_ingestion.run_signal_ingestion",
        lambda *args, **kwargs: SimpleNamespace(
            signal_urls_seen=10,
            signal_urls_added=1,
            stubs_created=1,
            signal_hits_bumped=0,
            errors=["Commit failed: deadlock detected"],
        ),
    )
    monkeypatch.setattr(
        "app.scheduler.tasks_content_events.run_content_event_dispatch_job",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.alerting_service.alert_scheduler_job_issue",
        lambda **kwargs: alerts.append(kwargs) or True,
    )

    run_signal_ingestion_job()

    assert alerts == [
        {
            "job_id": "signal_ingestion",
            "issue_type": "deadlock_detected",
            "details": "Commit failed: deadlock detected",
            "severity": "critical",
        }
    ]
