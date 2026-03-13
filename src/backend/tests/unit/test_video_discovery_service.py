from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import video_discovery_service as discovery_module
from app.services.video_discovery_service import VideoDiscoveryService

pytestmark = [pytest.mark.unit]


class _QuotaBudget:
    def __init__(self, *, begin_window: bool = True):
        self.begin_window = begin_window
        self.window_calls = []

    def begin_search_window(self, surface: str) -> bool:
        self.window_calls.append(surface)
        return self.begin_window


class _DummyYouTubeClient:
    def __init__(self, *, begin_window: bool = True):
        self.quota_budget = _QuotaBudget(begin_window=begin_window)
        self.search_calls = []
        self.trending_calls = []

    def fetch_search_candidates(
        self,
        query: str,
        *,
        region_code: str = "US",
        max_results: int = 10,
        surface: str = "videos",
        published_after=None,
    ):
        self.search_calls.append((query, region_code, max_results, surface, published_after))
        return []

    def fetch_trending_candidates(
        self, *, region_code: str = "US", max_results: int = 20, surface: str = "videos"
    ):
        self.trending_calls.append((region_code, max_results, surface))
        return []


def _service(client: _DummyYouTubeClient) -> VideoDiscoveryService:
    db = SimpleNamespace(commit=lambda: None)
    service = VideoDiscoveryService(db, client)
    service.repo = SimpleNamespace(record_run=lambda **_kwargs: None)
    return service


def test_discover_skips_all_work_when_surface_has_no_remaining_inventory(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient()
    service = _service(client)

    entries = service.discover("videos", remaining_needed=0)

    assert entries == []
    assert client.search_calls == []
    assert client.trending_calls == []
    assert client.quota_budget.window_calls == []


def test_discover_uses_trending_only_when_deficit_is_below_search_threshold(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient()
    service = _service(client)

    entries = service.discover("videos", remaining_needed=5)

    assert entries == []
    assert client.search_calls == []
    assert client.trending_calls == [("US", 20, "videos")]
    assert client.quota_budget.window_calls == []


def test_discover_search_is_cooldown_gated_but_trending_still_runs(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient(begin_window=False)
    service = _service(client)

    entries = service.discover("reels", remaining_needed=14)

    assert entries == []
    assert client.search_calls == []
    assert client.quota_budget.window_calls == ["reels"]
    assert client.trending_calls == [
        ("US", 10, "reels"),
        ("GB", 10, "reels"),
        ("CA", 10, "reels"),
        ("IN", 10, "reels"),
    ]


def test_discover_runs_small_search_plan_when_deficit_is_high(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient(begin_window=True)
    service = _service(client)

    entries = service.discover("videos", remaining_needed=14)

    assert entries == []
    assert client.quota_budget.window_calls == ["videos"]
    assert [(call[1], call[3]) for call in client.search_calls] == [
        ("US", "videos"),
        ("US", "videos"),
    ]
    assert client.trending_calls == [
        ("US", 20, "videos"),
        ("GB", 20, "videos"),
        ("CA", 20, "videos"),
        ("IN", 20, "videos"),
    ]


def test_discover_uses_expanded_search_result_pages(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient(begin_window=True)
    service = _service(client)

    service.discover("reels", remaining_needed=10)

    assert client.search_calls
    assert all(call[2] == 25 for call in client.search_calls)
