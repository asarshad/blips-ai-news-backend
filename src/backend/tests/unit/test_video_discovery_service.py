from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integrations.youtube_channels import ChannelRole, ContentFormat, QualityTier
from app.integrations.youtube_client import VideoEntry
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
        self.channel_stats = {}

    def fetch_search_candidates(
        self,
        query: str,
        *,
        region_code: str = "US",
        max_results: int = 10,
        surface: str = "videos",
        search_order: str = "relevance",
        query_label: str | None = None,
        published_after=None,
    ):
        self.search_calls.append(
            (query, region_code, max_results, surface, search_order, query_label, published_after)
        )
        return []

    def fetch_trending_candidates(
        self, *, region_code: str = "US", max_results: int = 20, surface: str = "videos"
    ):
        self.trending_calls.append((region_code, max_results, surface))
        return []

    def fetch_channel_stats(self, channel_ids):
        return {channel_id: self.channel_stats.get(channel_id, {}) for channel_id in channel_ids}


def _service(client: _DummyYouTubeClient) -> VideoDiscoveryService:
    db = SimpleNamespace(commit=lambda: None)
    service = VideoDiscoveryService(db, client)
    service.repo = SimpleNamespace(
        get_many=lambda _channel_ids: {},
        record_run=lambda **_kwargs: None,
        upsert_discovered_channels=lambda _channels: [],
    )
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
    monkeypatch.setenv("YOUTUBE_REEL_TRENDING_ENABLED", "1")
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


def test_discover_reels_skips_trending_by_default(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    monkeypatch.delenv("YOUTUBE_REEL_TRENDING_ENABLED", raising=False)
    client = _DummyYouTubeClient(begin_window=True)
    service = _service(client)

    entries = service.discover("reels", remaining_needed=14)

    assert entries == []
    assert client.search_calls
    assert client.trending_calls == []


def test_discover_runs_small_search_plan_when_deficit_is_high(monkeypatch):
    monkeypatch.setattr(discovery_module, "bootstrap_video_source_profiles", lambda _db: None)
    client = _DummyYouTubeClient(begin_window=True)
    service = _service(client)

    entries = service.discover("videos", remaining_needed=14)

    assert entries == []
    assert client.quota_budget.window_calls == ["videos"]
    assert [(call[1], call[3], call[4]) for call in client.search_calls] == [
        ("US", "videos", "viewCount"),
        ("US", "videos", "viewCount"),
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
    assert all(call[4] == "viewCount" for call in client.search_calls)


def test_filter_candidates_rejects_low_trust_discovery_channels():
    client = _DummyYouTubeClient()
    client.channel_stats = {
        "channel-low": {"subscriber_count": 1200, "video_count": 8},
        "channel-strong": {"subscriber_count": 54000, "video_count": 180},
    }
    service = _service(client)

    weak = VideoEntry(
        title="Google Maps Reimagined in 60 seconds",
        video_url="https://www.youtube.com/watch?v=weak123",
        thumbnail_url="https://img.youtube.com/vi/weak123/default.jpg",
        summary="A quick update on Google Maps and AI directions.",
        source="Random Tech Daily",
        category="Technology",
        video_id="weak123",
        channel_id="channel-low",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        quality_tier=QualityTier.STANDARD,
        is_short=False,
        acquisition_lane="search",
        source_status="discovery",
        view_count=300,
        like_count=2,
        comment_count=0,
        views_per_hour=0.0,
        format_fit_score=1.0,
    )
    strong = VideoEntry(
        title="Google Maps Gemini update explained",
        video_url="https://www.youtube.com/watch?v=strong123",
        thumbnail_url="https://img.youtube.com/vi/strong123/default.jpg",
        summary="A solid explainer on the latest Google Maps AI features.",
        source="Trusted Tech Lab",
        category="Technology",
        video_id="strong123",
        channel_id="channel-strong",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        quality_tier=QualityTier.STANDARD,
        is_short=False,
        acquisition_lane="search",
        source_status="discovery",
        view_count=12000,
        like_count=240,
        comment_count=35,
        views_per_hour=180.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([weak, strong], "videos")

    assert [entry.video_id for entry in accepted] == ["strong123"]
    assert counters["quality"] == 1


def test_filter_candidates_keeps_curated_core_channel_even_without_engagement():
    client = _DummyYouTubeClient()
    service = _service(client)

    entry = VideoEntry(
        title="What is your Tech Gripe?",
        video_url="https://www.youtube.com/shorts/WVUn4j2DaTY",
        thumbnail_url="https://img.youtube.com/vi/WVUn4j2DaTY/default.jpg",
        summary="",
        source="Linus Tech Tips",
        category="Technology",
        video_id="WVUn4j2DaTY",
        channel_id="UCXuqSBlHAE6Xw-yeJA0Tunw",
        channel_role=ChannelRole.SHORTS,
        content_format=ContentFormat.SHORTS,
        quality_tier=QualityTier.PREMIUM,
        is_short=True,
        acquisition_lane="curated",
        source_status="core",
        view_count=0,
        like_count=0,
        comment_count=0,
        views_per_hour=0.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "reels")

    assert [video.video_id for video in accepted] == ["WVUn4j2DaTY"]
    assert counters["quality"] == 0


def test_filter_candidates_rejects_story_led_zero_view_discovery_channel():
    client = _DummyYouTubeClient()
    client.channel_stats = {
        "channel-story": {"subscriber_count": 26000, "video_count": 120},
    }
    service = _service(client)

    entry = VideoEntry(
        title="Google Maps Reimagined in a Decade",
        video_url="https://www.youtube.com/watch?v=story123",
        thumbnail_url="https://img.youtube.com/vi/story123/default.jpg",
        summary="A random hot take on Google Maps.",
        source="Random Media Daily",
        category="Technology",
        video_id="story123",
        channel_id="channel-story",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        quality_tier=QualityTier.STANDARD,
        is_short=False,
        acquisition_lane="search",
        query_label="story-google-maps",
        source_status="discovery",
        view_count=0,
        like_count=4,
        comment_count=2,
        views_per_hour=0.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "videos")

    assert accepted == []
    assert counters["quality"] == 1


def test_filter_candidates_honors_profile_controls():
    client = _DummyYouTubeClient()
    client.channel_stats = {
        "channel-profile": {"subscriber_count": 88000, "video_count": 240},
    }
    service = _service(client)
    blocked_profile = SimpleNamespace(
        enabled=True,
        status="discovery",
        allow_search=False,
        allow_trending=True,
        daily_reel_cap=1,
    )
    service.repo = SimpleNamespace(
        get_many=lambda _channel_ids: {"channel-profile": blocked_profile},
        record_run=lambda **_kwargs: None,
        upsert_discovered_channels=lambda _channels: [],
    )

    entry = VideoEntry(
        title="Google Pixel camera update explained",
        video_url="https://www.youtube.com/watch?v=profile123",
        thumbnail_url="https://img.youtube.com/vi/profile123/default.jpg",
        summary="A concise explainer on the latest camera stack changes.",
        source="Trusted Tech Lab",
        category="Technology",
        video_id="profile123",
        channel_id="channel-profile",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.LONG_FORM,
        quality_tier=QualityTier.STANDARD,
        is_short=False,
        acquisition_lane="search",
        source_status="discovery",
        view_count=18000,
        like_count=420,
        comment_count=41,
        views_per_hour=220.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "videos")

    assert accepted == []
    assert counters["quality"] == 1


def test_filter_candidates_rejects_reels_from_profile_with_zero_reel_cap():
    client = _DummyYouTubeClient()
    client.channel_stats = {
        "channel-profile": {"subscriber_count": 88000, "video_count": 240},
    }
    service = _service(client)
    blocked_profile = SimpleNamespace(
        enabled=True,
        status="rotation",
        allow_search=True,
        allow_trending=True,
        daily_reel_cap=0,
    )
    service.repo = SimpleNamespace(
        get_many=lambda _channel_ids: {"channel-profile": blocked_profile},
        record_run=lambda **_kwargs: None,
        upsert_discovered_channels=lambda _channels: [],
    )

    entry = VideoEntry(
        title="Android 17 privacy shortcut",
        video_url="https://www.youtube.com/shorts/cap123",
        thumbnail_url="https://img.youtube.com/vi/cap123/default.jpg",
        summary="A quick look at the latest Android privacy shortcut.",
        source="Trusted Tech Lab",
        category="Technology",
        video_id="cap123",
        channel_id="channel-profile",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.SHORTS,
        quality_tier=QualityTier.STANDARD,
        is_short=True,
        acquisition_lane="search",
        source_status="rotation",
        view_count=18000,
        like_count=420,
        comment_count=41,
        views_per_hour=220.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "reels")

    assert accepted == []
    assert counters["quality"] == 1


def test_filter_candidates_rejects_low_story_discovery_reel_even_with_traction():
    client = _DummyYouTubeClient()
    client.channel_stats = {
        "channel-reel": {"subscriber_count": 125000, "video_count": 380},
    }
    service = _service(client)

    entry = VideoEntry(
        title="He Couldn't Play More Than 5 Minutes!!",
        video_url="https://www.youtube.com/shorts/reel123",
        thumbnail_url="https://img.youtube.com/vi/reel123/default.jpg",
        summary="A dramatic short with very little actual tech context.",
        source="Random Tech Shorts",
        category="Technology",
        video_id="reel123",
        channel_id="channel-reel",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.SHORTS,
        quality_tier=QualityTier.STANDARD,
        is_short=True,
        acquisition_lane="search",
        source_status="discovery",
        view_count=42000,
        like_count=3100,
        comment_count=180,
        views_per_hour=620.0,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "reels")

    assert accepted == []
    assert counters["quality"] == 1


def test_filter_candidates_rejects_non_english_title_when_api_language_missing(monkeypatch):
    client = _DummyYouTubeClient()
    service = _service(client)
    monkeypatch.setattr(discovery_module, "detect_language", lambda *_args, **_kwargs: ("hi", 0.99))

    entry = VideoEntry(
        title="Teri Siri ab Google chalayega",
        video_url="https://www.youtube.com/watch?v=lang123",
        thumbnail_url="https://img.youtube.com/vi/lang123/default.jpg",
        summary="Hindi explainer about Siri and Google.",
        source="Cyberdude",
        category="Technology",
        video_id="lang123",
        channel_id="channel-lang",
        channel_role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.SHORTS,
        quality_tier=QualityTier.STANDARD,
        is_short=True,
        acquisition_lane="search",
        source_status="discovery",
        default_language=None,
        format_fit_score=1.0,
    )

    accepted, counters = service._filter_candidates([entry], "reels")

    assert accepted == []
    assert counters["non_english"] == 1
