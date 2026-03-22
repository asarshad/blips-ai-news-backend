from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.video_source import VideoDiscoveryRun
from app.services import video_metrics_service as metrics_service
from app.services.inventory_service import SourceDistribution, Surface, SurfaceHealth, TierCounts
from app.services.video_metrics_service import (
    _comparison_window,
    _inventory_state,
    _surface_floor,
    _surface_window_hours,
    compute_video_lane_metrics,
    compute_video_supply_metrics,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_surface_floors_match_launch_gates():
    assert _surface_floor("videos") == 60
    assert _surface_floor("reels") == 40


def test_inventory_state_flags_channel_dominance():
    assert (
        _inventory_state("videos", fresh_inventory=80, top_count=20, dominant_count=5)
        == "imbalanced"
    )
    assert (
        _inventory_state("reels", fresh_inventory=60, top_count=20, dominant_count=4)
        == "imbalanced"
    )
    assert (
        _inventory_state("videos", fresh_inventory=80, top_count=20, dominant_count=3) == "healthy"
    )


def test_inventory_state_flags_recent_refresh_gap():
    assert (
        _inventory_state(
            "reels",
            fresh_inventory=60,
            top_count=20,
            dominant_count=2,
            recent_refresh_count=3,
            recent_refresh_threshold=12,
        )
        == "needs_refresh"
    )


def test_surface_windows_follow_curated_weekly_model():
    assert _surface_window_hours("videos") == 72
    assert _surface_window_hours("reels") == 168


def test_comparison_window_preserves_same_window_size_when_shifted():
    now = datetime(2026, 3, 13, 12, 0, 0)

    start, end = _comparison_window(now=now, window_hours=168, lag=timedelta(hours=24))

    assert end == datetime(2026, 3, 12, 12, 0, 0)
    assert start == datetime(2026, 3, 5, 12, 0, 0)


def test_compute_video_supply_metrics_merges_recent_refresh_health(monkeypatch):
    fake_now = datetime(2026, 3, 15, 12, 0, 0)

    monkeypatch.setattr(metrics_service, "refresh_video_source_health", lambda db: [])
    monkeypatch.setattr(metrics_service, "_profile_lookup", lambda db: {})
    monkeypatch.setattr(metrics_service, "load_baseline_snapshot", lambda baseline_tag=None: None)

    class _FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return fake_now

    monkeypatch.setattr(metrics_service, "datetime", _FrozenDatetime)

    def _fake_surface_metrics(db, surface, *, start, end=None, profiles=None):
        return {
            "inventory_window_hours": 168,
            "inventory_window_label": "7d",
            "floor_target": 60 if surface == "videos" else 40,
            "fresh_inventory_window": 79 if surface == "videos" else 54,
            "fresh_inventory_24h": 79 if surface == "videos" else 54,
            "median_age_top20_hours": 19.1 if surface == "videos" else 38.81,
            "p95_age_top20_hours": 32.0,
            "distinct_active_channels_window": 54 if surface == "videos" else 20,
            "distinct_active_channels_24h": 54 if surface == "videos" else 20,
            "dominant_channel": "channel-a",
            "dominant_channel_pct_top20": 5.0,
            "role_coverage_50": ["creator"],
            "category_coverage_50": ["Technology"],
            "official_share_top20": 10.0,
            "empty_feed_rate": 0.0,
            "caught_up_rate": 0.0,
            "clickbait_rejection_rate": 0.0,
            "duplicate_rejection_rate": 0.0,
            "inventory_state": "healthy",
            "top_items": [{"id": 1}],
        }

    monkeypatch.setattr(metrics_service, "_surface_metrics", _fake_surface_metrics)

    def _fake_surface_health(db, surface, now):
        if surface == Surface.REELS:
            return SurfaceHealth(
                surface=surface,
                tier_counts=TierCounts(tier_a=54, tier_b=0, tier_c=0),
                newest_item_age_seconds=1800,
                oldest_tier_a_age_seconds=86400,
                reservoir_count=155,
                min_fresh_threshold=40,
                source_distribution=SourceDistribution(counts={"Reel Source": 10}),
                recent_refresh_count=3,
                recent_refresh_threshold=12,
                refresh_window_hours=24,
                reservoir_threshold=320,
                is_healthy=False,
                issues=["Recent refresh below minimum: 3 < 12 in last 24h"],
            )
        return SurfaceHealth(
            surface=surface,
            tier_counts=TierCounts(tier_a=79, tier_b=0, tier_c=0),
            newest_item_age_seconds=1200,
            oldest_tier_a_age_seconds=7200,
            reservoir_count=220,
            min_fresh_threshold=60,
            source_distribution=SourceDistribution(counts={"Video Source": 10}),
            recent_refresh_count=16,
            recent_refresh_threshold=8,
            refresh_window_hours=36,
            reservoir_threshold=240,
            is_healthy=True,
            issues=[],
        )

    monkeypatch.setattr(metrics_service, "compute_surface_health", _fake_surface_health)

    payload = compute_video_supply_metrics(object())

    assert payload["surfaces"]["videos"]["recent_refresh_count"] == 16
    assert payload["surfaces"]["videos"]["inventory_state"] == "healthy"
    assert payload["surfaces"]["reels"]["recent_refresh_count"] == 3
    assert payload["surfaces"]["reels"]["recent_refresh_threshold"] == 12
    assert payload["surfaces"]["reels"]["refresh_window_hours"] == 24
    assert payload["surfaces"]["reels"]["issues"] == [
        "Recent refresh below minimum: 3 < 12 in last 24h"
    ]
    assert payload["surfaces"]["reels"]["inventory_state"] == "needs_refresh"


def test_compute_video_lane_metrics_supports_query_breakdown():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoDiscoveryRun.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    now = datetime.utcnow()

    db.add_all(
        [
            VideoDiscoveryRun(
                lane="search",
                surface="videos",
                query_label="ai-models",
                candidate_count=20,
                duplicate_rejections=1,
                clickbait_rejections=2,
                filtered_non_english=3,
                filtered_live=1,
                filtered_off_topic=2,
                filtered_format=1,
                run_started_at=now - timedelta(hours=1),
            ),
            ContentItem(
                type=ContentType.VIDEO,
                source="YouTube",
                source_url="https://youtube.com/watch?v=abc123",
                title="OpenAI Gemini Claude update",
                dedupe_key="yt:abc123",
                simhash="abc123",
                curation_status=ContentStatus.PROMOTED,
                discovered_via="yt_search:ai-models",
                acquisition_lane="search",
                channel_id="channel-1",
                published_at=now - timedelta(hours=2),
                created_at=now - timedelta(hours=1),
                is_suppressed=False,
            ),
        ]
    )
    db.commit()

    payload = compute_video_lane_metrics(db, hours=24, breakdown="query")

    row = payload["surfaces"]["videos"][0]
    assert payload["breakdown"] == "query"
    assert row["query_label"] == "ai-models"
    assert row["candidates"] == 20
    assert row["promoted"] == 1
    assert row["filtered_non_english"] == 3
    assert row["filtered_off_topic"] == 2


def test_compute_video_lane_metrics_query_breakdown_uses_blank_rate_without_candidates():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoDiscoveryRun.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    now = datetime.utcnow()

    db.add(
        ContentItem(
            type=ContentType.VIDEO,
            source="YouTube",
            source_url="https://youtube.com/watch?v=legacy123",
            title="Legacy search promotion",
            dedupe_key="yt:legacy123",
            simhash="legacy123",
            curation_status=ContentStatus.PROMOTED,
            discovered_via="yt_search:ai-models",
            acquisition_lane="search",
            channel_id="channel-1",
            published_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=1),
            is_suppressed=False,
        )
    )
    db.commit()

    payload = compute_video_lane_metrics(db, hours=24, breakdown="query")

    row = payload["surfaces"]["videos"][0]
    assert row["query_label"] == "ai-models"
    assert row["candidates"] == 0
    assert row["promoted"] == 1
    assert row["promotion_rate"] is None
