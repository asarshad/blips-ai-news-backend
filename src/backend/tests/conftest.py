import json
import os
import random
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-openapi-snapshot",
        action="store_true",
        default=False,
        help="Update the OpenAPI snapshot used by contract tests.",
    )


@pytest.fixture(autouse=True)
def _test_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Harden test environment for unit/contract tests.

    Integration tests intentionally override this and may require real DB/Redis.
    """
    is_integration = request.node.get_closest_marker("integration") is not None

    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SKIP_CREATE_TABLES", "true")
    if not is_integration:
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")
    else:
        monkeypatch.delenv("SKIP_STARTUP_CHECKS", raising=False)

    # Avoid accidental calls to real providers.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    random.seed(0)


@pytest.fixture
def fixture_text() -> callable:
    """Load a UTF-8 fixture from tests/fixtures/ by relative path."""

    def _load(rel_path: str) -> str:
        path = FIXTURES_DIR / rel_path
        return path.read_text(encoding="utf-8")

    return _load


@pytest.fixture
def fixture_json() -> callable:
    def _load(rel_path: str):
        path = FIXTURES_DIR / rel_path
        return json.loads(path.read_text(encoding="utf-8"))

    return _load
