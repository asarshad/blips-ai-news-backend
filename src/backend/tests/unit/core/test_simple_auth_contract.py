from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.session_auth import require_session_token


def test_protected_routes_reject_missing_bearer_token():
    app = FastAPI()

    @app.get("/protected")
    def protected(_session=Depends(require_session_token)):
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing Authorization header"}
