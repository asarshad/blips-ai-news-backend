import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


def test_openapi_hidden_when_docs_disabled():
    from app.core.config import settings
    from app.main import app

    original = settings.DOCS_ENABLED
    settings.DOCS_ENABLED = False
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/openapi.json")
    finally:
        settings.DOCS_ENABLED = original

    assert resp.status_code == 404
