# Backend testing

This repo uses pytest with a strict "no real internet" default.

## Setup

From `src/backend/`:

- Create/activate a venv
- Install deps: `pip install -r requirements.txt -r requirements-dev.txt`

## Suites

- Unit tests (fast, offline): `pytest tests/unit`
- Contract tests (OpenAPI snapshot): `pytest tests/contract`
- Update OpenAPI snapshot (intentional): `pytest tests/contract --update-openapi-snapshot`
- QA gates (coverage + missing tests): `python scripts/qa_gates.py --min-coverage 50`

## Integration tests (Docker)

Integration tests use `testcontainers` (Postgres + Redis). They are expected to run in a clean pytest process:

- `pytest tests/integration --force-enable-socket`

Notes:
- The default pytest config disables sockets to prevent accidental external calls. Docker/testcontainers needs local socket access.
- If Docker isn’t available, integration tests will be skipped with a message.
