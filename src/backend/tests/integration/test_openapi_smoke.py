import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


def test_app_starts_with_ephemeral_db_and_redis():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
