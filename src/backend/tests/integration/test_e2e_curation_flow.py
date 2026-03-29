"""E2E curation flow: ingest -> review -> approve -> feed."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]

ADMIN_KEY = "qa-admin-key"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """Create a test client with admin auth enabled."""
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")
    monkeypatch.setenv("SKIP_CREATE_TABLES", "true")

    from app.core.config import settings
    from app.main import app

    settings.ADMIN_API_KEY = ADMIN_KEY

    with TestClient(app) as test_client:
        yield test_client


def _auth_headers(client: TestClient) -> dict[str, str]:
    """Bootstrap an anonymous session and return bearer auth headers."""
    resp = client.post(
        "/api/v1/auth/session",
        json={"platform": "ios", "app_version": "1.0.0"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _insert_content(*, title: str, score: float, promoted: bool) -> int:
    from app.db.base import SessionLocal
    from app.models.content import ContentItem, ContentStatus, ContentType

    unique = uuid.uuid4().hex[:10]
    db = SessionLocal()
    try:
        item = ContentItem(
            type=ContentType.ARTICLE,
            source="integration.test",
            source_url=f"https://integration.test/{unique}",
            canonical_url=f"https://integration.test/{unique}",
            canonical_key=f"itest:{unique}",
            published_at=datetime.utcnow() - timedelta(minutes=5),
            title=title,
            description="integration description",
            summary="integration summary",
            ai_processed=True,
            topics=["ai", "testing"],
            entities=["integration"],
            quality_score=0.8,
            trend_score=0.7,
            recency_score=0.9,
            global_score=score,
            is_suppressed=False,
            curation_status=ContentStatus.PROMOTED if promoted else ContentStatus.CANDIDATE,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return int(item.id)
    finally:
        db.close()


class TestE2ECurationFlow:
    def test_ingest_review_promote_then_feed(self, client: TestClient):
        auth_headers = _auth_headers(client)

        # Ingest two rows: one already promoted, one still candidate.
        promoted_id = _insert_content(
            title="Baseline promoted item",
            score=0.60,
            promoted=True,
        )
        candidate_id = _insert_content(
            title="Candidate waiting for approval",
            score=0.95,
            promoted=False,
        )

        # Before approval, playlist should include promoted item but exclude candidate.
        before = client.get(
            "/api/v1/session/playlist",
            params={"type": "ARTICLE", "size": 20, "refresh": True},
            headers=auth_headers,
        )
        assert before.status_code == 200
        before_ids = [item["id"] for item in before.json()["items"]]
        assert promoted_id in before_ids
        assert candidate_id not in before_ids

        # Review queue should expose the candidate row to editors.
        review = client.get(
            "/api/v1/admin/editorial/content",
            params={"type": "ARTICLE", "page_size": 100},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert review.status_code == 200
        review_ids = [item["id"] for item in review.json()["items"]]
        assert candidate_id in review_ids

        # Approve (promote) candidate via admin endpoint.
        approve = client.post(
            f"/api/v1/admin/editorial/content/{candidate_id}/promote",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert approve.status_code == 200
        assert approve.json()["curation_status"] == "PROMOTED"

        # After approval, refreshed playlist should surface candidate at the top.
        after = client.get(
            "/api/v1/session/playlist",
            params={"type": "ARTICLE", "size": 20, "refresh": True},
            headers=auth_headers,
        )
        assert after.status_code == 200
        after_ids = [item["id"] for item in after.json()["items"]]
        assert candidate_id in after_ids
        assert after_ids[0] == candidate_id
