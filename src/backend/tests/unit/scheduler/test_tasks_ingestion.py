import sys
from types import SimpleNamespace

from app.scheduler import tasks_ingestion
from app.scheduler.job_stats import JobStats


def test_run_curation_ingestion_runs_video_discovery(monkeypatch):
    checkpoint_calls = []
    discovery_calls = []
    db = object()

    monkeypatch.setitem(
        sys.modules,
        "app.ingestion.checkpointing",
        SimpleNamespace(
            run_checkpointed_ingestion=lambda db, redis_client=None: (
                checkpoint_calls.append((db, redis_client)) or {"status": "ok"}
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.ingestion.service",
        SimpleNamespace(
            run_video_discovery_ingestion=lambda db: (
                discovery_calls.append(db)
                or {"videos_ingested": 2, "reels_ingested": 1, "errors": 0}
            )
        ),
    )
    monkeypatch.setattr(tasks_ingestion, "get_redis", lambda: None)
    monkeypatch.setattr(tasks_ingestion, "youtube_discovery_enabled", lambda: True)

    stats = JobStats(job_name="fetch_news")
    tasks_ingestion._run_curation_ingestion_with_stats(db, stats)

    assert checkpoint_calls == [(db, None)]
    assert discovery_calls == [db]
    assert stats.items_processed == 3
    assert stats.errors == []
