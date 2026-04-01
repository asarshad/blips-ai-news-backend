from __future__ import annotations

from types import SimpleNamespace

from app.scheduler import tasks_health


class _SurfaceKey:
    def __init__(self, value: str):
        self.value = value


def test_check_inventory_health_alerts_only_unhealthy_surfaces(monkeypatch):
    alerts: list[dict[str, object]] = []

    class _FakeDB:
        def close(self):
            return None

    health = SimpleNamespace(
        surfaces={
            _SurfaceKey("articles"): SimpleNamespace(
                is_healthy=True,
                issues=[],
                recent_refresh_count=15,
                recent_refresh_threshold=10,
                newest_item_age_seconds=120,
            ),
            _SurfaceKey("reels"): SimpleNamespace(
                is_healthy=False,
                issues=["Recent refresh below minimum"],
                recent_refresh_count=2,
                recent_refresh_threshold=12,
                newest_item_age_seconds=3600,
            ),
        }
    )

    monkeypatch.setattr(tasks_health, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        "app.services.inventory_service.get_cached_inventory_health",
        lambda db, force_refresh=False: health,
    )
    monkeypatch.setattr(
        "app.services.alerting_service.alert_inventory_surface_degraded",
        lambda **kwargs: alerts.append(kwargs) or True,
    )

    tasks_health.check_inventory_health()

    assert alerts == [
        {
            "surface": "reels",
            "issues": ["Recent refresh below minimum"],
            "recent_refresh_count": 2,
            "recent_refresh_threshold": 12,
            "newest_item_age_seconds": 3600,
        }
    ]
