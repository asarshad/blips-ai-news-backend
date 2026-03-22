"""Scheduler cadence tests for continuous ingestion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _job_call_from_calls(mock_scheduler: MagicMock, job_id: str):
    for call in mock_scheduler.add_job.call_args_list:
        kwargs = call.kwargs
        if kwargs.get("id") == job_id:
            return call
    raise AssertionError(f"{job_id} job was not registered")


def _fetch_trigger_from_calls(mock_scheduler: MagicMock):
    return _job_call_from_calls(mock_scheduler, "fetch_news").args[1]


@patch("app.scheduler.BackgroundScheduler")
def test_ingestion_minutes_clamped_low(mock_scheduler_cls, monkeypatch):
    """Values below 5 minutes should clamp to 5."""
    mock_scheduler = MagicMock()
    mock_scheduler_cls.return_value = mock_scheduler
    monkeypatch.setenv("INGESTION_SCHEDULER_MINUTES", "4")

    from app.scheduler import init_scheduler

    init_scheduler()
    trigger = _fetch_trigger_from_calls(mock_scheduler)
    assert int(trigger.interval.total_seconds() // 60) == 5


@patch("app.scheduler.BackgroundScheduler")
def test_ingestion_minutes_clamped_high(mock_scheduler_cls, monkeypatch):
    """Values above 15 minutes should clamp to 15."""
    mock_scheduler = MagicMock()
    mock_scheduler_cls.return_value = mock_scheduler
    monkeypatch.setenv("INGESTION_SCHEDULER_MINUTES", "30")

    from app.scheduler import init_scheduler

    init_scheduler()
    trigger = _fetch_trigger_from_calls(mock_scheduler)
    assert int(trigger.interval.total_seconds() // 60) == 15


@patch("app.scheduler.BackgroundScheduler")
def test_ingestion_minutes_uses_in_range_value(mock_scheduler_cls, monkeypatch):
    """In-range values should be used directly."""
    mock_scheduler = MagicMock()
    mock_scheduler_cls.return_value = mock_scheduler
    monkeypatch.setenv("INGESTION_SCHEDULER_MINUTES", "10")

    from app.scheduler import init_scheduler

    init_scheduler()
    trigger = _fetch_trigger_from_calls(mock_scheduler)
    assert int(trigger.interval.total_seconds() // 60) == 10


@patch("app.scheduler.BackgroundScheduler")
def test_backfill_job_registered_with_default_interval(mock_scheduler_cls, monkeypatch):
    """Backfill should run on a recurring cadence so image repair is automatic."""
    mock_scheduler = MagicMock()
    mock_scheduler_cls.return_value = mock_scheduler
    monkeypatch.delenv("BACKFILL_INTERVAL_HOURS", raising=False)

    from app.scheduler import init_scheduler

    init_scheduler()
    trigger = _job_call_from_calls(mock_scheduler, "backfill_job").args[1]
    assert int(trigger.interval.total_seconds() // 3600) == 6
