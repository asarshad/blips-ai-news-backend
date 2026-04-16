from types import SimpleNamespace

from app.api.routes import admin as admin_routes


def test_trigger_scripted_maintenance_delegates_to_stable_script(monkeypatch):
    calls = {}
    fake_module = SimpleNamespace(
        run=lambda payload: {
            "echo": payload,
            "job": "article_image_backfill",
        }
    )

    def _fake_import(module_name: str):
        calls["imported"] = module_name
        return fake_module

    monkeypatch.setattr(
        admin_routes.importlib,
        "import_module",
        _fake_import,
    )
    monkeypatch.setattr(admin_routes.importlib, "reload", lambda module: module)

    response = admin_routes.trigger_scripted_maintenance(
        admin_routes.ScriptedMaintenanceRequest(payload={"limit": 25})
    )

    assert calls["imported"] == "scripts.operator_backfill_job"
    assert response["status"] == "ok"
    assert response["script"] == "scripts.operator_backfill_job"
    assert response["result"] == {
        "echo": {"limit": 25},
        "job": "article_image_backfill",
    }
