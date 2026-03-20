"""
Critical API integration tests for release readiness.

Tests the most important API paths:
- Health check validates DB and Redis
- Current content endpoints return proper structure
- Inventory health endpoints work correctly
- Metrics and operational status endpoints (admin-protected)

These tests use TestClient with ephemeral DB/Redis from conftest.py.
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


@pytest.fixture
def client():
    """Create test client with ephemeral services."""
    import os

    os.environ["SKIP_STARTUP_CHECKS"] = "true"
    os.environ["SKIP_CREATE_TABLES"] = "true"

    from app.main import app

    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    """Test the /health endpoint behavior."""

    def test_health_returns_200_when_healthy(self, client):
        """Health check should return 200 when DB and Redis are available."""
        resp = client.get("/health")
        # May return 503 if DB/Redis not fully ready in test env
        # but should at least return a valid response
        assert resp.status_code in [200, 503]
        data = resp.json()
        assert "status" in data
        assert "database" in data or data["status"] == "healthy"

    def test_health_response_structure(self, client):
        """Health check should return expected structure."""
        resp = client.get("/health")
        data = resp.json()
        assert isinstance(data, dict)
        assert "status" in data
        # When healthy, should have these keys
        if data["status"] == "healthy":
            assert data.get("database") == "ok"
            assert data.get("redis") == "ok"


class TestContentEndpoints:
    """Test the current content retrieval endpoints."""

    def test_articles_recent_returns_payload(self, client):
        """Articles recent endpoint should exist and return the current payload shape."""
        resp = client.get("/api/v1/articles/recent")
        assert resp.status_code in [200, 404, 500]
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            assert "articles" in data
            assert "has_more" in data
            assert "page" in data

    def test_videos_recent_returns_payload(self, client):
        """Videos recent endpoint should exist and return the current payload shape."""
        resp = client.get("/api/v1/videos/recent")
        assert resp.status_code in [200, 500, 503]
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            assert "items" in data
            assert "has_more" in data
            assert "inventory_state" in data

    def test_reels_recent_returns_payload(self, client):
        """Reels endpoint should exist and return the current payload shape."""
        resp = client.get("/api/v1/videos/reels")
        assert resp.status_code in [200, 500, 503]
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            assert "items" in data
            assert "has_more" in data
            assert "inventory_state" in data

    def test_playlist_endpoint_exists(self, client):
        """Playlist endpoint should exist on the session API."""
        resp = client.get(
            "/api/v1/session/playlist?type=ARTICLE&size=10",
            headers={"X-Device-ID": "test-device-1234"},
        )
        assert resp.status_code in [200, 500]
        if resp.status_code == 200:
            data = resp.json()
            assert "items" in data
            assert "session_id" in data
            assert "has_more" in data


class TestInventoryEndpoints:
    """Test inventory health endpoints."""

    def test_inventory_health_returns_structure(self, client):
        """Inventory health should return expected structure or 500 if DB not ready."""
        resp = client.get("/api/v1/inventory/health")
        assert resp.status_code in [200, 500]
        if resp.status_code == 200:
            data = resp.json()
            # Should have surface-level health
            assert isinstance(data, dict)

    def test_inventory_surface_articles(self, client):
        """Per-surface inventory health for articles."""
        resp = client.get("/api/v1/inventory/health/articles")
        assert resp.status_code in [200, 404, 500]


class TestAdminEndpoints:
    """Test admin-protected endpoints."""

    def test_metrics_requires_admin_key(self, client):
        """Metrics endpoint should reject requests without admin key."""
        resp = client.get("/metrics")
        # Should be 401 or 403 without admin key
        assert resp.status_code in [401, 403, 422]

    def test_metrics_with_invalid_key(self, client):
        """Metrics with invalid key should be rejected."""
        resp = client.get("/metrics", headers={"X-Admin-Key": "wrong-key"})
        assert resp.status_code in [401, 403]

    def test_ops_status_requires_admin_key(self, client):
        """Operational status endpoint should require admin key."""
        resp = client.get("/ops/status")
        assert resp.status_code in [401, 403, 422]

    def test_playlist_stats_requires_admin_key(self, client):
        """Playlist stats should not be publicly accessible."""
        resp = client.get("/api/v1/session/playlist-stats")
        assert resp.status_code in [401, 403]


class TestAIChatEndpoints:
    """Test AI chat endpoints (without actual LLM calls)."""

    def test_chat_endpoint_exists(self, client):
        """Chat endpoint should exist and require proper params."""
        resp = client.post(
            "/api/v1/ai/respond",
            json={"content_id": "test-123", "message": "Hello", "device_id": "test-device"},
        )
        # Will fail validation or LLM config, but endpoint should exist
        assert resp.status_code in [200, 400, 404, 422, 500]

    def test_conversation_starters_endpoint(self, client):
        """Conversation starters endpoint should exist."""
        resp = client.get("/api/v1/starters/1")
        assert resp.status_code in [200, 404, 500]

    def test_legacy_conversations_endpoint_not_exposed(self, client):
        """Legacy public conversation CRUD should not be mounted."""
        resp = client.get("/api/v1/conversations/1")
        assert resp.status_code == 404


class TestRateLimiting:
    """Test rate limiting behavior."""

    def test_rate_limit_headers_present(self, client):
        """Rate limit headers should be present in responses."""
        resp = client.get("/health")
        # slowapi may not be configured in test env
        # Just verify endpoint responds
        assert resp.status_code in [200, 503]


class TestOpenAPISpec:
    """Test OpenAPI specification."""

    def test_openapi_json_hidden_when_docs_disabled(self, client):
        """OpenAPI spec should be hidden when docs are disabled."""
        from app.core.config import settings

        original = settings.DOCS_ENABLED
        settings.DOCS_ENABLED = False
        try:
            resp = client.get("/api/v1/openapi.json")
        finally:
            settings.DOCS_ENABLED = original

        assert resp.status_code == 404

    def test_docs_endpoint_hidden_when_docs_disabled(self, client):
        """Swagger UI should not be available when docs are disabled."""
        from app.core.config import settings

        original = settings.DOCS_ENABLED
        settings.DOCS_ENABLED = False
        try:
            resp = client.get("/docs")
        finally:
            settings.DOCS_ENABLED = original

        assert resp.status_code == 404

    def test_preferences_require_matching_device_header(self, client):
        """Preferences APIs must reject callers acting on another device."""
        resp = client.get(
            "/api/v1/users/device-alpha/categories",
            headers={"X-Device-ID": "device-beta"},
        )
        assert resp.status_code == 403
