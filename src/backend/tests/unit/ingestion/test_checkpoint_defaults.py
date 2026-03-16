from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from enum import Enum
from types import ModuleType, SimpleNamespace

from app.ingestion import checkpoint_defaults
from app.ingestion.checkpoint_defaults import build_defaults, get_reel_auto_pause_decisions
from app.models.content import ContentType


class _ContentFormat(str, Enum):
    LONG_FORM = "long_form"
    SHORTS = "shorts"
    MIXED = "mixed"


@dataclass
class _Channel:
    name: str
    content_format: _ContentFormat
    daily_cap: int


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows

    def count(self):
        return len(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)


def _install_fake_integrations(monkeypatch, *, channels, rss_feeds=None):
    pkg = ModuleType("app.integrations")
    pkg.__path__ = []  # mark as package

    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: SimpleNamespace(feed_configs=rss_feeds or [])  # type: ignore[attr-defined]

    yt_channels_mod = ModuleType("app.integrations.youtube_channels")
    yt_channels_mod.ContentFormat = _ContentFormat  # type: ignore[attr-defined]

    yt_client_mod = ModuleType("app.integrations.youtube_client")
    yt_client_mod.YouTubeClient = lambda: SimpleNamespace(channel_configs=channels)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_channels", yt_channels_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_client_mod)


def test_build_defaults_uses_curated_channel_caps(monkeypatch):
    channels = [
        _Channel("Long Feed", _ContentFormat.LONG_FORM, 1),
        _Channel("Mixed Feed", _ContentFormat.MIXED, 2),
        _Channel("Shorts Feed", _ContentFormat.SHORTS, 2),
    ]
    _install_fake_integrations(monkeypatch, channels=channels)
    monkeypatch.delenv("INGESTION_TARGET_DEFAULTS", raising=False)

    defaults = build_defaults(day_utc=date(2026, 3, 9))
    targets = {(d.source_type, d.feed_name): d.target for d in defaults}

    assert targets[("youtube_video", "Long Feed")] == 1
    assert targets[("youtube_video", "Mixed Feed")] == 1
    assert targets[("youtube_reel", "Mixed Feed")] == 1
    assert targets[("youtube_reel", "Shorts Feed")] == 2


def test_build_defaults_keeps_mixed_split_for_unoverridden_channels(monkeypatch):
    channels = [_Channel("Custom Mixed Feed", _ContentFormat.MIXED, 5)]
    _install_fake_integrations(monkeypatch, channels=channels)
    monkeypatch.delenv("INGESTION_TARGET_DEFAULTS", raising=False)

    defaults = build_defaults(day_utc=date(2026, 3, 9))
    targets = {(d.source_type, d.feed_name): d.target for d in defaults}

    assert targets[("youtube_video", "Custom Mixed Feed")] == 2
    assert targets[("youtube_reel", "Custom Mixed Feed")] == 3


def test_build_defaults_always_creates_youtube_rows_regardless_of_inventory(monkeypatch):
    """YouTube rows are always created for continuous ingestion (Phase A).

    _should_fill_surface is no longer used to gate YouTube row creation.
    """
    channels = [
        _Channel("Long Feed", _ContentFormat.LONG_FORM, 1),
        _Channel("Mixed Feed", _ContentFormat.MIXED, 2),
        _Channel("Shorts Feed", _ContentFormat.SHORTS, 2),
    ]
    rss_feeds = [SimpleNamespace(name="RSS Feed", daily_cap=2)]
    _install_fake_integrations(monkeypatch, channels=channels, rss_feeds=rss_feeds)
    monkeypatch.delenv("INGESTION_TARGET_DEFAULTS", raising=False)

    defaults = build_defaults(day_utc=date(2026, 3, 9))
    source_types = {d.source_type for d in defaults}

    assert "rss" in source_types
    assert "youtube_video" in source_types
    assert "youtube_reel" in source_types


def test_reel_guardrail_is_disabled_in_curated_only_mode(monkeypatch):
    db = _FakeDb(
        [
            SimpleNamespace(
                feed_name="No Yield Feed",
                day_utc=date(2026, 3, 8),
                items_attempted=65,
                items_ingested=0,
            )
        ]
    )
    monkeypatch.setattr(checkpoint_defaults.settings, "YOUTUBE_CURATED_ONLY", True)

    paused = get_reel_auto_pause_decisions(
        db=db,
        day_utc=date(2026, 3, 9),
        feed_names=["No Yield Feed"],
    )

    assert paused == {}


def test_reel_guardrail_pauses_after_five_low_conversion_days_when_enabled(monkeypatch):
    db = _FakeDb(
        [
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 8),
                items_attempted=100,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 7),
                items_attempted=90,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 6),
                items_attempted=80,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 5),
                items_attempted=75,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 4),
                items_attempted=70,
                items_ingested=1,
            ),
        ]
    )
    monkeypatch.setattr(checkpoint_defaults.settings, "YOUTUBE_CURATED_ONLY", False)

    paused = get_reel_auto_pause_decisions(
        db=db,
        day_utc=date(2026, 3, 9),
        feed_names=["Low Conversion Feed"],
    )

    assert "Low Conversion Feed" in paused
    assert "conversion<2%" in paused["Low Conversion Feed"]


def test_fresh_promoted_count_applies_curated_only_policy(monkeypatch):
    db = _FakeDb([object(), object(), object()])
    seen = {}

    def _apply_policy(query, *, content_type=None):
        seen["content_type"] = content_type
        return query

    monkeypatch.setattr(checkpoint_defaults, "apply_content_policy", _apply_policy)

    count = checkpoint_defaults._fresh_promoted_count(db, ContentType.VIDEO, hours=168)

    assert count == 3
    assert seen["content_type"] == ContentType.VIDEO


def test_should_fill_surface_when_recent_video_refresh_is_stale(monkeypatch):
    counts = {
        (ContentType.VIDEO, 168): 79,
        (ContentType.VIDEO, checkpoint_defaults.settings.VIDEOS_REFRESH_PUBLISHED_HOURS): 0,
    }

    monkeypatch.setattr(
        checkpoint_defaults,
        "_fresh_promoted_count",
        lambda _db, content_type, *, hours: counts[(content_type, hours)],
    )

    should_fill = checkpoint_defaults._should_fill_surface(object(), ContentType.VIDEO)

    assert should_fill is True
