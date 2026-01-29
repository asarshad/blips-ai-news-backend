import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.contract, pytest.mark.unit]


SNAPSHOT_PATH = Path(__file__).parent / "openapi_snapshot.json"


def _normalized_json(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def test_openapi_snapshot_is_stable(request: pytest.FixtureRequest):
    # Import after pytest autouse env fixture disables scheduler/create-tables.
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200

    current = resp.json()

    if request.config.getoption("--update-openapi-snapshot"):
        SNAPSHOT_PATH.write_text(_normalized_json(current) + "\n", encoding="utf-8")
        return

    if not SNAPSHOT_PATH.exists():
        pytest.fail(
            "OpenAPI snapshot missing. Generate it by running: "
            "pytest -m contract --update-openapi-snapshot"
        )

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert _normalized_json(current) == _normalized_json(expected)
