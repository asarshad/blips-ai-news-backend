import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.testclient import TestClient

from app.api.admin.ui import admin_ui_auth_redirect_response
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

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        admin_redirect = admin_ui_auth_redirect_response(
            request,
            status_code=exc.status_code,
        )
        if admin_redirect is not None:
            return admin_redirect
        return await http_exception_handler(request, exc)

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


def test_admin_ui_missing_auth_redirects_to_login_for_deep_links(client):
    response = client.get("/api/v1/admin/ui/submit", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == (
        "/api/v1/admin/ui/login?next=%2Fapi%2Fv1%2Fadmin%2Fui%2Fsubmit"
    )


def test_admin_ui_query_param_no_longer_authenticates(client):
    response = client.get("/api/v1/admin/ui/submit?key=test-admin-key", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == (
        "/api/v1/admin/ui/login?next=%2Fapi%2Fv1%2Fadmin%2Fui%2Fsubmit%3Fkey%3Dtest-admin-key"
    )


def test_admin_ui_post_auth_failures_redirect_to_login_without_replaying_post():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/ui/action/42/approve",
            "headers": [],
            "query_string": b"",
        },
    )

    response = admin_ui_auth_redirect_response(request, status_code=401)

    assert response is not None
    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/admin/ui/login"
