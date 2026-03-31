from __future__ import annotations

from types import SimpleNamespace

from app.core import observability


class _FakeRedisPool:
    max_connections = 15
    _in_use_connections = {object(), object()}
    _available_connections = {object()}


class _FakeRedisClient:
    def __init__(self, *, lock_owner: bytes | None, ttl: int = 90):
        self._lock_owner = lock_owner
        self._ttl = ttl

    def ping(self):
        return True

    def get(self, key: str):  # noqa: ARG002
        return self._lock_owner

    def ttl(self, key: str):  # noqa: ARG002
        return self._ttl


def test_get_redis_pool_stats_uses_shared_pool_accessor(monkeypatch):
    monkeypatch.setattr(
        "app.core.dependencies.get_redis_pool",
        lambda: _FakeRedisPool(),
    )

    stats = observability.get_redis_pool_stats()

    assert stats == {
        "status": "ok",
        "max_connections": 15,
        "current_connections": 2,
        "available_connections": 1,
    }


def test_get_db_pool_stats_reads_sqlalchemy_engine_pool(monkeypatch):
    fake_pool = SimpleNamespace(
        size=lambda: 3,
        checkedin=lambda: 2,
        checkedout=lambda: 1,
        overflow=lambda: 0,
    )
    monkeypatch.setattr(
        "app.db.base.engine",
        SimpleNamespace(pool=fake_pool),
    )

    stats = observability.get_db_pool_stats()

    assert stats == {
        "status": "ok",
        "size": 3,
        "checkedin": 2,
        "checkedout": 1,
        "overflow": 0,
    }


def test_get_runtime_health_reports_degraded_when_external_scheduler_lock_missing(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INGESTION_ENABLED", "true")
    monkeypatch.setattr(observability, "_database_health_check", lambda: {"status": "ok"})
    monkeypatch.setattr(observability, "_redis_health_check", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "app.core.dependencies.get_redis",
        lambda: _FakeRedisClient(lock_owner=None, ttl=-2),
    )
    monkeypatch.setattr(
        observability,
        "get_ingestion_health_status",
        lambda: {
            "status": "ok",
            "is_stalled": False,
            "last_successful_ingestion_at": "2026-03-31T00:00:00",
        },
    )

    health = observability.get_runtime_health()

    assert health["status"] == "degraded"
    assert health["checks"]["scheduler"]["status"] == "degraded"
    assert health["checks"]["scheduler"]["leader_lock_present"] is False


def test_get_runtime_health_reports_healthy_when_scheduler_lock_visible(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INGESTION_ENABLED", "true")
    monkeypatch.setattr(observability, "_database_health_check", lambda: {"status": "ok"})
    monkeypatch.setattr(observability, "_redis_health_check", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "app.core.dependencies.get_redis",
        lambda: _FakeRedisClient(lock_owner=b"worker-1", ttl=120),
    )
    monkeypatch.setattr(
        observability,
        "get_ingestion_health_status",
        lambda: {
            "status": "ok",
            "is_stalled": False,
            "last_successful_ingestion_at": "2026-03-31T00:00:00",
        },
    )

    health = observability.get_runtime_health()

    assert health["status"] == "healthy"
    assert health["checks"]["scheduler"]["status"] == "ok"
    assert health["checks"]["scheduler"]["leader_lock_owner"] == "worker-1"
