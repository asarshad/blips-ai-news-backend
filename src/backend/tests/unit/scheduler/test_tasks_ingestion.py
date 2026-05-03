import sys
from types import SimpleNamespace

from app.scheduler import tasks_ingestion
from app.scheduler.job_stats import JobStats


def test_run_curation_ingestion_runs_video_discovery(monkeypatch):
    checkpoint_calls = []
    discovery_calls = []
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

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
    monkeypatch.setitem(
        sys.modules,
        "app.scheduler.tasks_curation",
        SimpleNamespace(
            queue_content_clustering_request=lambda db, *, trigger="fetch_news": object(),
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


def test_fetch_and_process_news_skips_when_ingestion_disabled(monkeypatch):
    ingestion_ran = []

    monkeypatch.setattr(
        tasks_ingestion.feature_flags,
        "is_enabled",
        lambda name: False,
    )
    monkeypatch.setattr(
        tasks_ingestion,
        "_run_curation_ingestion_with_stats",
        lambda _db, _stats: ingestion_ran.append(True),
    )

    tasks_ingestion.fetch_and_process_news()

    assert ingestion_ran == []


def test_run_curation_ingestion_enqueues_clustering_event(monkeypatch):
    checkpoint_calls = []
    clustering_enqueue_calls = []
    promotion_calls = []
    commits = []
    db = SimpleNamespace(commit=lambda: commits.append(True), rollback=lambda: None)

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
        "app.scheduler.tasks_curation",
        SimpleNamespace(
            queue_content_clustering_request=lambda db, *, trigger="fetch_news": (
                clustering_enqueue_calls.append((db, trigger)) or object()
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.scheduler.tasks_promotion",
        SimpleNamespace(
            run_promotion_job=lambda *, trigger="scheduled": promotion_calls.append(trigger),
        ),
    )
    monkeypatch.setattr(tasks_ingestion, "get_redis", lambda: None)
    monkeypatch.setattr(tasks_ingestion, "youtube_discovery_enabled", lambda: False)

    stats = JobStats(job_name="fetch_news")
    tasks_ingestion._run_curation_ingestion_with_stats(db, stats)

    assert checkpoint_calls == [(db, None)]
    # Clustering is now event-driven: ingestion enqueues a single clustering
    # request which the clustering lane drains asynchronously. Promotion still
    # flows through the per-item outbox + content-event lane.
    assert clustering_enqueue_calls == [(db, "fetch_news")]
    assert commits == [True]
    assert promotion_calls == []
