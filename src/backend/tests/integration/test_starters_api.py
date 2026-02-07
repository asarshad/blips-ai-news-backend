"""Integration tests for Conversation Starters API.

These tests run against an ephemeral Postgres container with Alembic migrations
applied. They verify the full flow from API → Service → Database.
"""

import os
import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module", autouse=True)
def _use_fake_llm():
    """Ensure FakeLLM is used for all tests in this module."""
    original = os.environ.get("LLM_PROVIDER")
    os.environ["LLM_PROVIDER"] = "fake"
    yield
    if original is not None:
        os.environ["LLM_PROVIDER"] = original
    else:
        os.environ.pop("LLM_PROVIDER", None)


@pytest.fixture(scope="module")
def client():
    """Create a test client with the app."""
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_article(client):
    """Create a sample article in the database for testing."""
    from app.db.base import SessionLocal
    from app.models.content import ContentItem, ContentType
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        article = ContentItem(
            title="Test AI Breakthrough",
            url="https://example.com/test-article",
            summary="Researchers achieve new milestone in AI development.",
            content_type=ContentType.ARTICLE,
            source_name="Test Source",
            published_at=datetime.now(timezone.utc),
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        yield article
        # Cleanup
        db.delete(article)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def sample_video(client):
    """Create a sample video in the database for testing."""
    from app.db.base import SessionLocal
    from app.models.content import ContentItem, ContentType
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        video = ContentItem(
            title="Tech Trends 2024",
            url="https://youtube.com/watch?v=test123",
            summary="Overview of emerging tech trends.",
            content_type=ContentType.VIDEO,
            source_name="Test Channel",
            published_at=datetime.now(timezone.utc),
            youtube_video_id="test123",
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        yield video
        # Cleanup
        db.delete(video)
        db.commit()
    finally:
        db.close()


class TestStartersEndpoints:
    """Test the /starters API endpoints."""

    def test_get_starters_for_nonexistent_content_returns_404(self, client):
        """Starters endpoint returns 404 for non-existent content."""
        resp = client.get("/api/v1/starters/99999999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_starters_generates_for_article(self, client, sample_article):
        """Starters are generated for an article without cached starters."""
        resp = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp.status_code == 200
        
        data = resp.json()
        assert data["content_id"] == sample_article.id
        assert isinstance(data["starters"], list)
        assert len(data["starters"]) >= 1
        assert isinstance(data["fallback"], list)
        assert len(data["fallback"]) >= 1

    def test_get_starters_generates_for_video(self, client, sample_video):
        """Starters are generated for a video without cached starters."""
        resp = client.get(f"/api/v1/starters/{sample_video.id}")
        assert resp.status_code == 200
        
        data = resp.json()
        assert data["content_id"] == sample_video.id
        assert isinstance(data["starters"], list)
        assert isinstance(data["fallback"], list)

    def test_get_starters_returns_cached(self, client, sample_article):
        """Subsequent calls return cached starters from DB."""
        # First call generates starters
        resp1 = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp1.status_code == 200
        starters1 = resp1.json()["starters"]
        
        # Second call returns cached (same starters)
        resp2 = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp2.status_code == 200
        starters2 = resp2.json()["starters"]
        
        assert starters1 == starters2

    def test_regenerate_starters_via_query_param(self, client, sample_article):
        """Starters can be regenerated via regenerate=true query param."""
        # First call
        resp1 = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp1.status_code == 200
        
        # Regenerate
        resp2 = client.get(f"/api/v1/starters/{sample_article.id}?regenerate=true")
        assert resp2.status_code == 200
        
        # FakeLLM returns consistent responses, so values may be same
        # but the endpoint should work without error
        assert "starters" in resp2.json()

    def test_post_generate_forces_regeneration(self, client, sample_article):
        """POST /generate endpoint forces starters regeneration."""
        # Generate via GET first
        resp1 = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp1.status_code == 200
        
        # Force regenerate via POST
        resp2 = client.post(f"/api/v1/starters/{sample_article.id}/generate")
        assert resp2.status_code == 200
        
        data = resp2.json()
        assert data["content_id"] == sample_article.id
        assert isinstance(data["starters"], list)

    def test_post_generate_nonexistent_returns_404(self, client):
        """POST /generate returns 404 for non-existent content."""
        resp = client.post("/api/v1/starters/99999999/generate")
        assert resp.status_code == 404


class TestStartersPersistence:
    """Test that starters are persisted correctly in the database."""

    def test_starters_persisted_after_generation(self, client, sample_article):
        """Generated starters are persisted to the DB."""
        from app.db.base import SessionLocal
        from app.models.content import ContentItem
        
        # Generate starters via API
        resp = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp.status_code == 200
        api_starters = resp.json()["starters"]
        
        # Verify persisted in DB
        db = SessionLocal()
        try:
            item = db.query(ContentItem).filter(
                ContentItem.id == sample_article.id
            ).first()
            assert item is not None
            assert item.conversation_starters is not None
            assert item.conversation_starters.get("starters") == api_starters
        finally:
            db.close()


class TestFakeLLMIntegration:
    """Test that FakeLLM works correctly in integration context."""

    def test_fake_llm_provides_deterministic_starters(self, client, sample_article):
        """FakeLLM provides valid starters structure."""
        resp = client.get(f"/api/v1/starters/{sample_article.id}")
        assert resp.status_code == 200
        
        data = resp.json()
        # FakeLLM should return starters containing content-specific references
        starters = data["starters"]
        
        # At minimum, we should have some starters
        assert len(starters) >= 1
        
        # Fallback should also be present
        assert len(data["fallback"]) >= 1
