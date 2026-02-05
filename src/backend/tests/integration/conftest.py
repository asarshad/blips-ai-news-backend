import os
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from testcontainers.core.docker_client import DockerClient
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from alembic import command as alembic_command

# Repository layout: <repo>/src/backend/tests/integration/conftest.py
# We want <repo>/src/backend as the backend root (contains alembic.ini).
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _require_docker() -> None:
    try:
        DockerClient().client.ping()
    except Exception as exc:  # pragma: no cover
        msg = str(exc)
        if "pytest-socket" in msg or "A test tried to use socket.socket.connect" in msg:
            pytest.skip(
                "Integration tests require Docker and local socket access. "
                "Re-run with `pytest tests/integration --force-enable-socket`. "
                f"(details: {exc})"
            )
        pytest.skip(f"Docker not available for integration tests: {exc}")


@pytest.fixture(scope="session")
def postgres_url() -> str:
    _require_docker()
    # Pin image for deterministic schema behavior.
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> str:
    _require_docker()
    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session", autouse=True)
def _integration_env(postgres_url: str, redis_url: str) -> None:
    """Configure env vars for integration tests.

    Note: These tests are intended to be run in a clean pytest process
    (e.g., `pytest tests/integration`) because the app uses a module-level
    settings singleton in `app.core.config`.
    """
    os.environ["DATABASE_URL"] = postgres_url
    os.environ["REDIS_URL"] = redis_url

    # Integration tests should exercise real startup behavior.
    os.environ.pop("SKIP_STARTUP_CHECKS", None)

    # Avoid background jobs / create_all; we validate migrations instead.
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["SKIP_CREATE_TABLES"] = "true"


@pytest.fixture(scope="session", autouse=True)
def _run_migrations(postgres_url: str) -> None:
    """Apply Alembic migrations against the ephemeral Postgres container."""
    alembic_ini = BACKEND_ROOT / "alembic.ini"
    alembic_cfg = AlembicConfig(str(alembic_ini))

    # Ensure script_location is absolute (relative paths break when cwd != ini dir).
    alembic_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))

    # Ensure the DB URL is the container URL.
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_url)

    alembic_command.upgrade(alembic_cfg, "head")
