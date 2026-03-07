"""Scheduler cadence tests for continuous ingestion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _fetch_trigger_from_calls(mock_scheduler: MagicMock):
    for call in mock_scheduler.add_job.call_args_list:
        kwargs = call.kwargs
        if kwargs.get("id") == "fetch_news":
            # add_job(func, trigger, id=...)
            return call.args[1]
    raise AssertionError("fetch_news job was not registered")


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
