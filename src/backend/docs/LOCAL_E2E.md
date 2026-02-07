# Local End-to-End Testing Guide

This document describes how to run local end-to-end tests for the backend, ensuring all integrations work correctly without calling external services.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Make or bash shell

## Quick Start

### Option 1: E2E Script (Recommended)

The `scripts/e2e_local.sh` script runs the complete E2E workflow:

```bash
cd src/backend
chmod +x scripts/e2e_local.sh
./scripts/e2e_local.sh
```

This script:
1. Starts isolated Postgres + Redis containers (ports 5433, 6380)
2. Runs Alembic migrations
3. Seeds test content
4. Starts the API server with FakeLLM
5. Validates all endpoints
6. Cleans up on exit

### Option 2: Manual Steps

#### 1. Start Test Infrastructure

```bash
cd src/backend
docker-compose -f docker-compose.test.yml up -d
```

This starts:
- **PostgreSQL** on port `5433` (avoids conflict with dev DB on 5432)
- **Redis** on port `6380` (avoids conflict with dev Redis on 6379)

#### 2. Run Migrations

```bash
export DATABASE_URL="postgresql://test:test@localhost:5433/test_db"
alembic upgrade head
```

#### 3. Start API with FakeLLM

```bash
export DATABASE_URL="postgresql://test:test@localhost:5433/test_db"
export REDIS_URL="redis://localhost:6380/0"
export LLM_PROVIDER="fake"
export SCHEDULER_ENABLED="false"

uvicorn app.main:app --reload --port 8001
```

#### 4. Validate Endpoints

```bash
# Health check
curl http://localhost:8001/api/v1/health

# Seed content and test starters
# (See scripts/e2e_local.sh for full validation steps)
```

#### 5. Cleanup

```bash
docker-compose -f docker-compose.test.yml down -v
```

## Test Modes

### Unit Tests (No Docker Required)

```bash
pytest tests/unit -v
```

### Integration Tests (Uses Testcontainers)

```bash
pytest tests/integration -v --force-enable-socket
```

Integration tests use [testcontainers](https://testcontainers.com/) to spin up ephemeral Postgres and Redis containers automatically.

### Contract Tests

```bash
pytest tests/contract -v
```

## FakeLLM Provider

The FakeLLM provider (`app/integrations/fake_llm.py`) is a deterministic LLM implementation for testing:

```python
# Enable FakeLLM
export LLM_PROVIDER="fake"
```

Features:
- Returns deterministic, content-aware responses
- No API calls to OpenAI/Mistral
- Tracks call count for assertions
- Generates valid JSON for starters

Usage in tests:
```python
import os
os.environ["LLM_PROVIDER"] = "fake"

from app.integrations.llm_client import LLMClient
client = LLMClient()
# Uses FakeLLMClient internally
```

## Docker Compose Test Configuration

The `docker-compose.test.yml` file provides isolated test infrastructure:

| Service    | Port  | Purpose           |
|------------|-------|-------------------|
| test_db    | 5433  | Test PostgreSQL   |
| test_redis | 6380  | Test Redis        |

### Environment Variables

| Variable           | Test Value                                          |
|-------------------|-----------------------------------------------------|
| `DATABASE_URL`    | `postgresql://test:test@localhost:5433/test_db`    |
| `REDIS_URL`       | `redis://localhost:6380/0`                          |
| `LLM_PROVIDER`    | `fake`                                              |
| `SCHEDULER_ENABLED` | `false`                                           |

## CI Integration

The GitHub Actions workflows use these same patterns:

- **Unit/Contract Tests**: No Docker, fast feedback
- **Integration Tests**: Testcontainers with ephemeral DB/Redis
- **Daily E2E**: Full docker-compose.test.yml flow

See `.github/workflows/backend_pr.yml` and `.github/workflows/backend_integration.yml`.

## Troubleshooting

### Port Conflicts

If ports 5433 or 6380 are in use:

```bash
# Find and kill process on port
lsof -i :5433
kill -9 <PID>

# Or change ports in docker-compose.test.yml
```

### Docker Not Available

Integration tests require Docker. If unavailable, they skip with a message:

```
SKIPPED: Docker not available for integration tests
```

### Migration Errors

If migrations fail, ensure you're pointing to the test database:

```bash
export DATABASE_URL="postgresql://test:test@localhost:5433/test_db"
alembic downgrade base
alembic upgrade head
```

## Test Data Seeding

The E2E script seeds sample content:

```bash
# Seed an article
curl -X POST http://localhost:8001/api/v1/content \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Article",
    "url": "https://example.com/test",
    "content_type": "article",
    "source_name": "Test"
  }'
```

For more complex seeding, see `scripts/seed_test_data.py` (if available) or the E2E script.
