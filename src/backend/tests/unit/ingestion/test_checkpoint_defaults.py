from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from enum import Enum
from types import ModuleType, SimpleNamespace

from app.ingestion.checkpoint_defaults import build_defaults, get_reel_auto_pause_decisions


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


def test_build_defaults_applies_explicit_reel_targets_to_total_30(monkeypatch):
    channels = [
        _Channel("The Verge", _ContentFormat.MIXED, 3),
        _Channel("Technology Connections Shorts", _ContentFormat.SHORTS, 2),
        _Channel("Karl Conrad", _ContentFormat.MIXED, 2),
        _Channel("SuperSaf", _ContentFormat.MIXED, 2),
        _Channel("Sam Beckman", _ContentFormat.MIXED, 2),
        _Channel("Mrwhosetheboss", _ContentFormat.MIXED, 2),
        _Channel("Unbox Therapy", _ContentFormat.MIXED, 2),
        _Channel("JerryRigEverything", _ContentFormat.MIXED, 2),
        _Channel("Marques Brownlee (MKBHD)", _ContentFormat.MIXED, 2),
        _Channel("ShortCircuit", _ContentFormat.MIXED, 3),
        _Channel("TechLinked", _ContentFormat.MIXED, 2),
        _Channel("Android Developers", _ContentFormat.MIXED, 2),
        _Channel("Tech Vision", _ContentFormat.SHORTS, 5),
        _Channel("Linus Tech Tips", _ContentFormat.MIXED, 2),
        _Channel("Fireship", _ContentFormat.MIXED, 2),
        _Channel("Matt Wolfe", _ContentFormat.MIXED, 2),
        _Channel("Jeff Geerling", _ContentFormat.MIXED, 3),
    ]
    _install_fake_integrations(monkeypatch, channels=channels)
    monkeypatch.delenv("INGESTION_TARGET_DEFAULTS", raising=False)

    defaults = build_defaults(day_utc=date(2026, 3, 9))
    reel_targets = {d.feed_name: d.target for d in defaults if d.source_type == "youtube_reel"}
    video_targets = {d.feed_name: d.target for d in defaults if d.source_type == "youtube_video"}

    assert sum(reel_targets.values()) == 30
    assert reel_targets["The Verge"] == 4
    assert reel_targets["Technology Connections Shorts"] == 4
    assert reel_targets["Marques Brownlee (MKBHD)"] == 2
    assert reel_targets["ShortCircuit"] == 1
    assert reel_targets["TechLinked"] == 1

    assert "Android Developers" not in reel_targets
    assert "Tech Vision" not in reel_targets
    assert "Linus Tech Tips" not in reel_targets
    assert "Fireship" not in reel_targets
    assert "Matt Wolfe" not in reel_targets
    assert "Jeff Geerling" not in reel_targets

    # Converted MIXED channels keep original video throughput via explicit overrides.
    assert video_targets["Marques Brownlee (MKBHD)"] == 2
    assert video_targets["ShortCircuit"] == 3
    assert video_targets["TechLinked"] == 2


def test_build_defaults_keeps_mixed_split_for_unoverridden_channels(monkeypatch):
    channels = [_Channel("Custom Mixed Feed", _ContentFormat.MIXED, 5)]
    _install_fake_integrations(monkeypatch, channels=channels)
    monkeypatch.delenv("INGESTION_TARGET_DEFAULTS", raising=False)

    defaults = build_defaults(day_utc=date(2026, 3, 9))
    targets = {(d.source_type, d.feed_name): d.target for d in defaults}

    assert targets[("youtube_video", "Custom Mixed Feed")] == 2
    assert targets[("youtube_reel", "Custom Mixed Feed")] == 3


def test_reel_guardrail_pauses_after_exhausted_day():
    db = _FakeDb(
        [
            SimpleNamespace(
                feed_name="No Yield Feed",
                day_utc=date(2026, 3, 8),
                items_attempted=35,
                items_ingested=0,
            )
        ]
    )

    paused = get_reel_auto_pause_decisions(
        db=db,
        day_utc=date(2026, 3, 9),
        feed_names=["No Yield Feed"],
    )

    assert "No Yield Feed" in paused
    assert "attempted>=30 and inserted=0" in paused["No Yield Feed"]


def test_reel_guardrail_pauses_after_three_low_conversion_days():
    db = _FakeDb(
        [
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 8),
                items_attempted=40,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 7),
                items_attempted=45,
                items_ingested=1,
            ),
            SimpleNamespace(
                feed_name="Low Conversion Feed",
                day_utc=date(2026, 3, 6),
                items_attempted=50,
                items_ingested=2,
            ),
        ]
    )

    paused = get_reel_auto_pause_decisions(
        db=db,
        day_utc=date(2026, 3, 9),
        feed_names=["Low Conversion Feed"],
    )

    assert "Low Conversion Feed" in paused
    assert "conversion<5%" in paused["Low Conversion Feed"]
