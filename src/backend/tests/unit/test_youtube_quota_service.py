from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core import youtube_quota as quota_module
from app.core.youtube_quota import YouTubeQuotaBudget

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clear_local_store(monkeypatch):
    quota_module._LOCAL_STORE.clear()
    monkeypatch.setattr(YouTubeQuotaBudget, "_get_redis_client", lambda self: None)
    yield
    quota_module._LOCAL_STORE.clear()


def _fixed_now() -> datetime:
    return datetime(2026, 3, 12, 18, 0, tzinfo=timezone.utc)


def test_try_reserve_respects_total_and_bucket_limits(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_DAILY_BUDGET_UNITS", "5")
    monkeypatch.setenv("YOUTUBE_API_SEARCH_DAILY_BUDGET_UNITS", "3")
    monkeypatch.setenv("YOUTUBE_API_DURATION_DAILY_BUDGET_UNITS", "2")

    budget = YouTubeQuotaBudget(now_provider=_fixed_now)

    assert budget.try_reserve(2, bucket="search") is True
    assert budget.try_reserve(1, bucket="search") is True
    assert budget.try_reserve(1, bucket="search") is False

    assert budget.try_reserve(2, bucket="duration") is True
    assert budget.try_reserve(1, bucket="duration") is False
    assert budget.remaining_units() == 0


def test_begin_search_window_enforces_cooldown(monkeypatch):
    monkeypatch.setenv("YOUTUBE_SEARCH_MIN_INTERVAL_MINUTES", "180")
    budget = YouTubeQuotaBudget(now_provider=_fixed_now)

    assert budget.begin_search_window("videos") is True
    assert budget.begin_search_window("videos") is False
    assert budget.begin_search_window("reels") is True


def test_lock_out_until_reset_marks_budget_unavailable():
    budget = YouTubeQuotaBudget(now_provider=_fixed_now)

    assert budget.is_locked_out() is False

    budget.lock_out_until_reset(reason="quotaExceeded")

    assert budget.is_locked_out() is True
    assert budget.try_reserve(1) is False
