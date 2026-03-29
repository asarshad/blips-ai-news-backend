from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.session_auth import require_session_token
from app.models.content import UserProfile
from app.models.device_session import DeviceSession


def _db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UserProfile.__table__.create(engine)
    DeviceSession.__table__.create(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_protected_routes_reject_missing_bearer_token():
    app = FastAPI()

    @app.get("/protected")
    def protected(_session=Depends(require_session_token)):
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing Authorization header"}


def test_prod_origin_guard_rejects_api_requests_without_edge_secret(monkeypatch):
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")

    from app import main as main_module

    class _RedisStub:
        def ping(self):
            return True

    monkeypatch.setattr(main_module, "get_redis", lambda: _RedisStub())
    monkeypatch.setattr(main_module.settings, "ENV", "prod")
    monkeypatch.setattr(main_module.settings, "EDGE_ORIGIN_SECRET", "edge-secret")
    monkeypatch.setattr(main_module.settings, "EDGE_SESSION_BOOTSTRAP_SECRET", "bootstrap-secret")
    monkeypatch.setattr(
        main_module.settings,
        "SESSION_AUTH_ACCESS_SECRET",
        "test-secret-key-with-at-least-thirty-two-bytes",
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/auth/session",
            json={"platform": "ios", "app_version": "1.0.0"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Direct origin access denied"}


def test_prod_bootstrap_requires_edge_verification_header(monkeypatch):
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")

    from app import main as main_module

    class _RedisStub:
        def ping(self):
            return True

    monkeypatch.setattr(main_module, "get_redis", lambda: _RedisStub())
    monkeypatch.setattr(main_module.settings, "ENV", "prod")
    monkeypatch.setattr(main_module.settings, "EDGE_ORIGIN_SECRET", "edge-secret")
    monkeypatch.setattr(main_module.settings, "EDGE_SESSION_BOOTSTRAP_SECRET", "bootstrap-secret")
    monkeypatch.setattr(
        main_module.settings,
        "SESSION_AUTH_ACCESS_SECRET",
        "test-secret-key-with-at-least-thirty-two-bytes",
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/auth/session",
            json={"platform": "ios", "app_version": "1.0.0"},
            headers={"X-Edge-Origin-Secret": "edge-secret"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Session bootstrap not allowed"}


def test_prod_bootstrap_succeeds_with_edge_headers(monkeypatch):
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")

    from app import main as main_module

    class _RedisStub:
        def ping(self):
            return True

    monkeypatch.setattr(main_module, "get_redis", lambda: _RedisStub())
    monkeypatch.setattr(main_module.settings, "ENV", "prod")
    monkeypatch.setattr(main_module.settings, "EDGE_ORIGIN_SECRET", "edge-secret")
    monkeypatch.setattr(main_module.settings, "EDGE_SESSION_BOOTSTRAP_SECRET", "bootstrap-secret")
    monkeypatch.setattr(
        main_module.settings,
        "SESSION_AUTH_ACCESS_SECRET",
        "test-secret-key-with-at-least-thirty-two-bytes",
    )
    db = _db_session()
    main_module.app.dependency_overrides[get_db] = lambda: db

    try:
        with TestClient(main_module.app) as client:
            response = client.post(
                "/api/v1/auth/session",
                json={"platform": "ios", "app_version": "1.0.0"},
                headers={
                    "X-Edge-Origin-Secret": "edge-secret",
                    "X-Edge-Session-Bootstrap": "bootstrap-secret",
                },
            )
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)
        db.close()

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"].startswith("dev_")
    assert body["access_token"]
    assert body["refresh_token"]
