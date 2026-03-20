import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin.ui import router as admin_ui_router
from app.core.auth import build_admin_ui_session_token, is_valid_admin_ui_session
from app.core.config import settings

pytestmark = [pytest.mark.unit]


@pytest.fixture
def client():
    original_admin_key = settings.ADMIN_API_KEY
    original_env = settings.ENV
    settings.ADMIN_API_KEY = "test-admin-key"
    settings.ENV = "dev"

    app = FastAPI()
    app.include_router(admin_ui_router, prefix="/api/v1")

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.ADMIN_API_KEY = original_admin_key
        settings.ENV = original_env


def test_admin_ui_session_token_is_distinct_from_admin_key():
    original_admin_key = settings.ADMIN_API_KEY
    settings.ADMIN_API_KEY = "test-admin-key"
    try:
        token = build_admin_ui_session_token()
        assert token
        assert token != "test-admin-key"
        assert is_valid_admin_ui_session(token)
    finally:
        settings.ADMIN_API_KEY = original_admin_key


def test_admin_ui_root_redirects_to_login_when_unauthenticated(client):
    response = client.get("/api/v1/admin/ui/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/api/v1/admin/ui/login"


def test_admin_ui_login_sets_cookie_and_redirects_clean_url(client):
    response = client.post(
        "/api/v1/admin/ui/login",
        data={"admin_key": "test-admin-key", "next": "/api/v1/admin/ui/submit"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/admin/ui/submit"
    cookie_header = response.headers.get("set-cookie", "")
    assert "blips_admin_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header


def test_admin_ui_submit_page_uses_cookie_without_leaking_admin_key(client):
    login = client.post(
        "/api/v1/admin/ui/login",
        data={"admin_key": "test-admin-key"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    response = client.get("/api/v1/admin/ui/submit")

    assert response.status_code == 200
    assert "?key=" not in response.text
    assert 'name="key"' not in response.text
    assert "test-admin-key" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_admin_ui_query_param_no_longer_authenticates(client):
    response = client.get("/api/v1/admin/ui/submit?key=test-admin-key", follow_redirects=False)

    assert response.status_code == 401
