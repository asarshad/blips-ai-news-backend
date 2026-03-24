from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import share_target
from app.core.config import settings

pytestmark = [pytest.mark.unit]


class _FakeEditorialService:
    last_db = None
    last_submit_kwargs = None
    response = None

    def __init__(self, db):
        self.__class__.last_db = db

    @classmethod
    def reset(cls):
        cls.last_db = None
        cls.last_submit_kwargs = None
        cls.response = None

    def submit_url(self, **kwargs):
        self.__class__.last_submit_kwargs = kwargs
        return self.__class__.response


@pytest.fixture(autouse=True)
def restore_admin_share_token():
    original_token = settings.ADMIN_SHARE_TOKEN
    try:
        yield
    finally:
        settings.ADMIN_SHARE_TOKEN = original_token


@pytest.fixture
def client(monkeypatch):
    settings.ADMIN_SHARE_TOKEN = "shortcut-secret"
    _FakeEditorialService.reset()

    app = FastAPI()
    app.include_router(share_target.router, prefix="/api/v1")
    app.dependency_overrides[share_target.get_db] = lambda: "fake-db"
    monkeypatch.setattr(share_target, "EditorialService", _FakeEditorialService)

    with TestClient(app) as test_client:
        yield test_client


def test_share_target_submit_success(client):
    _FakeEditorialService.response = SimpleNamespace(
        content_id=91,
        duplicate=False,
        status="created",
        message="Manual video queued (id=91). Pending review.",
        content_type="VIDEO",
    )

    response = client.post(
        "/api/v1/admin/share-target/submit",
        headers={"X-Admin-Share-Token": "shortcut-secret"},
        json={
            "url": "https://www.youtube.com/watch?v=video123",
            "importance_level": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "content_id": 91,
        "duplicate": False,
        "status": "created",
        "message": "Manual video queued (id=91). Pending review.",
        "content_type": "VIDEO",
    }
    assert _FakeEditorialService.last_db == "fake-db"
    assert _FakeEditorialService.last_submit_kwargs == {
        "url": "https://www.youtube.com/watch?v=video123",
        "importance_level": 2,
        "actor": "ios_shortcut",
    }


def test_share_target_submit_duplicate_response(client):
    _FakeEditorialService.response = SimpleNamespace(
        content_id=14,
        duplicate=True,
        status="duplicate_exists",
        message="Duplicate found (id=14). No changes applied.",
        content_type="ARTICLE",
    )

    response = client.post(
        "/api/v1/admin/share-target/submit",
        headers={"X-Admin-Share-Token": "shortcut-secret"},
        json={
            "url": "https://example.com/story",
            "importance_level": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["status"] == "duplicate_exists"
    assert response.json()["content_type"] == "ARTICLE"


def test_share_target_submit_rejects_bad_token(client):
    response = client.post(
        "/api/v1/admin/share-target/submit",
        headers={"X-Admin-Share-Token": "wrong-secret"},
        json={
            "url": "https://example.com/story",
            "importance_level": 0,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid admin share token"}


def test_share_target_submit_fails_closed_when_token_unset(client):
    settings.ADMIN_SHARE_TOKEN = ""

    response = client.post(
        "/api/v1/admin/share-target/submit",
        headers={"X-Admin-Share-Token": "shortcut-secret"},
        json={
            "url": "https://example.com/story",
            "importance_level": 0,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Admin share-target endpoint is disabled (ADMIN_SHARE_TOKEN not configured)"
    }
