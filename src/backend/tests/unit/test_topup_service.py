import sys
from datetime import datetime
from types import SimpleNamespace

from app.services import topup_service
from app.services.inventory_service import InventoryHealth


def test_run_topup_runs_video_discovery(monkeypatch):
    db = SimpleNamespace(close=lambda: None)
    health_states = iter(
        [
            InventoryHealth(timestamp=datetime.utcnow(), surfaces={}, needs_topup=True),
            InventoryHealth(timestamp=datetime.utcnow(), surfaces={}, needs_topup=False),
        ]
    )
    checkpoint_calls = []
    discovery_calls = []

    monkeypatch.setitem(
        sys.modules,
        "app.core.dependencies",
        SimpleNamespace(get_redis=lambda: None),
    )
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
                discovery_calls.append(db) or {"videos_ingested": 1, "reels_ingested": 1}
            )
        ),
    )
    monkeypatch.setattr(
        topup_service, "get_cached_inventory_health", lambda *_a, **_k: next(health_states)
    )
    monkeypatch.setattr(topup_service, "invalidate_health_cache", lambda: None)
    monkeypatch.setattr(topup_service, "invalidate_tiered_feed_cache", lambda: None)
    monkeypatch.setattr(topup_service, "_release_topup_lock", lambda: None)
    monkeypatch.setattr(topup_service.settings, "TOPUP_MAX_RUNTIME_SECONDS", 60)
    monkeypatch.setattr(topup_service, "youtube_discovery_enabled", lambda: True)

    topup_service._run_topup(lambda: db)

    assert checkpoint_calls == [(db, None)]
    assert discovery_calls == [db]
