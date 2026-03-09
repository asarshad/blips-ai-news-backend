from __future__ import annotations

from datetime import date

from app.ingestion.scheduler import (
    IngestionScheduler,
    SchedulerConfig,
    TaskRef,
    build_fair_queue,
    load_scheduler_config,
)


class _FakeRedis:
    def __init__(self, held_keys: set[str] | None = None):
        self._held = held_keys or set()

    def exists(self, key: str) -> int:
        return 1 if key in self._held else 0


def test_build_fair_queue_interleaves_types():
    tasks = [
        TaskRef(1, "rss", "a"),
        TaskRef(2, "rss", "b"),
        TaskRef(3, "youtube_video", "v1"),
        TaskRef(4, "youtube_video", "v2"),
        TaskRef(5, "youtube_reel", "r1"),
        TaskRef(6, "youtube_reel", "r2"),
    ]

    q = build_fair_queue(tasks)
    got = [t.source_type for t in q]

    # Should alternate in rss/video/reel order while available.
    assert got[:3] == ["rss", "youtube_video", "youtube_reel"]
    assert got[3:] == ["rss", "youtube_video", "youtube_reel"]


def test_scheduler_respects_per_type_caps_and_lease_visibility():
    cfg = SchedulerConfig(
        max_workers=3,
        max_workers_article=1,
        max_workers_video=1,
        max_workers_reel=1,
        batch_size=10,
        loop_sleep_seconds=0.01,
    )

    scheduler = IngestionScheduler(day_utc=date(2026, 1, 29), redis_client=_FakeRedis(), config=cfg)

    tasks = [
        TaskRef(1, "rss", "a"),
        TaskRef(2, "rss", "b"),
        TaskRef(3, "youtube_video", "v1"),
        TaskRef(4, "youtube_reel", "r1"),
    ]
    scheduler.refresh(tasks=tasks)

    # Pop three tasks; should get one of each type due to caps.
    t1 = scheduler.pop_next_dispatchable()
    scheduler.mark_active(t1)
    t2 = scheduler.pop_next_dispatchable()
    scheduler.mark_active(t2)
    t3 = scheduler.pop_next_dispatchable()
    scheduler.mark_active(t3)

    assert {t1.source_type, t2.source_type, t3.source_type} == {
        "rss",
        "youtube_video",
        "youtube_reel",
    }

    # Now caps are full; further pops should return None.
    assert scheduler.pop_next_dispatchable() is None


def test_scheduler_skips_tasks_when_lease_exists():
    # Build a scheduler whose redis reports the rss lease as held.
    day = date(2026, 1, 29)
    from app.ingestion.leases import lease_key

    held = {lease_key(day.isoformat(), "rss", "a")}
    redis = _FakeRedis(held_keys=held)

    cfg = SchedulerConfig(
        max_workers=1,
        max_workers_article=1,
        max_workers_video=0,
        max_workers_reel=0,
        batch_size=10,
        loop_sleep_seconds=0.01,
    )

    scheduler = IngestionScheduler(day_utc=day, redis_client=redis, config=cfg)
    scheduler.refresh(tasks=[TaskRef(1, "rss", "a")])

    assert scheduler.pop_next_dispatchable() is None


def test_load_scheduler_config_preserves_article_capacity_when_explicit_caps_starve_it(
    monkeypatch,
):
    monkeypatch.setenv("INGESTION_MAX_WORKERS", "2")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_ARTICLE", "0")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_VIDEO", "2")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_REEL", "0")

    cfg = load_scheduler_config()

    assert cfg.max_workers == 2
    assert cfg.max_workers_article == 1
    assert cfg.max_workers_video == 1
    assert cfg.max_workers_reel == 0


def test_load_scheduler_config_rebalances_when_article_is_zero_and_caps_are_full(monkeypatch):
    monkeypatch.setenv("INGESTION_MAX_WORKERS", "3")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_ARTICLE", "0")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_VIDEO", "1")
    monkeypatch.setenv("INGESTION_MAX_WORKERS_REEL", "2")

    cfg = load_scheduler_config()

    assert cfg.max_workers == 3
    assert cfg.max_workers_article == 1
    assert cfg.max_workers_video == 1
    assert cfg.max_workers_reel == 1
