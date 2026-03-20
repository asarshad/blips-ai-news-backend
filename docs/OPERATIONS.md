# Operations Runbook

Current operational guide for the FastAPI backend in `src/backend/`.

## Deploy model

- Render deploys from `blips-ai-news-backend/src/backend`.
- Pushes to `main` are deployable and should be treated as production-affecting.
- Production is split into:
  - `blips-api` web service
  - `blips-worker` background worker
  - Render PostgreSQL
  - Render Redis
- Development uses a single `blips-dev-api` web service with the scheduler enabled.

Before pushing backend changes, run local CI from `src/backend/`:

```bash
./.venv/bin/python -m ruff check app tests
./.venv/bin/python -m ruff format --check app tests
./.venv/bin/python -m pytest <impacted scope>
```

## Access patterns

```bash
export HOST="https://blips-api.onrender.com"
export ADMIN_KEY="your-admin-api-key"
```

Auth rules:

- `/health` does not require auth.
- `/metrics`, `/ops/status`, `/api/v1/metrics/*`, `/api/v1/inventory/health*`, and `/api/v1/admin/*` require `X-Admin-Key`.
- The server-rendered admin UI is under `/api/v1/admin/ui/*` and uses a browser login form that sets an HTTP-only session cookie.

## Core operational endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | Liveness and dependency health |
| `GET /metrics` | `X-Admin-Key` | Service metrics and pool state |
| `GET /ops/status` | `X-Admin-Key` | Request, DB, and Redis status snapshot |
| `GET /api/v1/inventory/health` | `X-Admin-Key` | Inventory-health summary across content surfaces |
| `GET /api/v1/inventory/health/{surface}` | `X-Admin-Key` | Inventory-health drill-down |
| `GET /api/v1/metrics/sources` | `X-Admin-Key` | Source ingestion health |
| `GET /api/v1/metrics/video-sources` | `X-Admin-Key` | Video source health |
| `GET /api/v1/admin/maintenance/status` | `X-Admin-Key` | Retention/cleanup status |
| `POST /api/v1/admin/maintenance/cleanup` | `X-Admin-Key` | Manual cleanup trigger |
| `POST /api/v1/admin/trigger-fetch` | `X-Admin-Key` | Manual ingestion trigger |
| `POST /api/v1/admin/trigger-summarize` | `X-Admin-Key` | Manual AI retry trigger |
| `POST /api/v1/admin/youtube/reset-search-cooldown` | `X-Admin-Key` | Reset YouTube discovery cooldown keys |
| `GET /api/v1/admin/ui/` | browser login | Operator UI entry point |

## Quick commands

```bash
# Health
curl -s "$HOST/health" | jq

# Service metrics
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/metrics" | jq

# Ops status
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/ops/status" | jq

# Inventory health
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/inventory/health" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/inventory/health/articles" | jq

# Source diagnostics
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/metrics/sources" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/metrics/video-sources" | jq

# Manual ingestion / AI retry
curl -s -X POST -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/admin/trigger-fetch" | jq
curl -s -X POST -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/admin/trigger-summarize" | jq

# Maintenance
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/admin/maintenance/status" | jq
curl -s -X POST -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/admin/maintenance/cleanup" | jq
```

Admin UI:

```text
https://blips-api.onrender.com/api/v1/admin/ui/
```

## Environment expectations

Operationally important settings:

- `SCHEDULER_ENABLED=false` on the production API and `true` on the worker
- `INGESTION_ENABLED=true` on the worker
- `DOCS_ENABLED=false` in production
- `DEBUG_ROUTES_ENABLED=false` in production unless you intentionally expose debug routes
- `INGESTION_SCHEDULER_MINUTES` is the preferred continuous-ingestion cadence knob
- `LLM_DAILY_COST_CEILING` gates daily LLM spend

Reference docs:

- configuration details: [`docs/CONFIGURATION.md`](./CONFIGURATION.md)
- content ingestion behavior: [`docs/INGESTION.md`](./INGESTION.md)
- architecture: [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)

## Common incidents

### 1. Service health degradation

Symptoms:

- `/health` returns `503`
- `/metrics` or `/ops/status` show DB or Redis failures

Checks:

```bash
curl -s "$HOST/health" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/ops/status" | jq
```

Response:

1. Identify whether the failing dependency is PostgreSQL, Redis, or the app process.
2. Check Render service status and recent deploy logs.
3. If the API deploy is bad, roll back in Render.
4. If the DB or Redis service is degraded, confirm platform status before changing app config.

### 2. Ingestion stall

Symptoms:

- inventory health degrades
- `/api/v1/metrics/sources` shows problem feeds
- worker logs show ingestion disabled or repeated failures

Checks:

```bash
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/inventory/health" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/metrics/sources" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/admin/maintenance/status" | jq
```

Response:

1. Confirm the worker has `SCHEDULER_ENABLED=true`.
2. Confirm `INGESTION_ENABLED=true` and `INGESTION_CRON_DISABLED=false`.
3. If needed, trigger a one-off fetch with `/api/v1/admin/trigger-fetch`.
4. If YouTube discovery looks stale, reset cooldown keys with `/api/v1/admin/youtube/reset-search-cooldown`.

### 3. LLM quota or provider failure

Symptoms:

- AI chat or summaries fail while the rest of the API still works
- logs show provider errors or daily spend ceiling hits

Checks:

1. Confirm the active provider and key are set correctly.
2. Review recent logs for `LLM daily cost ceiling reached` or provider-specific failures.
3. Verify `LLM_DAILY_COST_CEILING` is not set too low for the current ingestion load.

Response:

1. Restore or rotate provider credentials if needed.
2. Increase `LLM_DAILY_COST_CEILING` only intentionally and with cost awareness.
3. Trigger `/api/v1/admin/trigger-summarize` after provider recovery if backlog exists.

### 4. Empty or weak inventory

Symptoms:

- playlist quality drops
- inventory health shows low fresh counts

Checks:

```bash
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/inventory/health" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$HOST/api/v1/metrics/video-sources" | jq
```

Response:

1. Identify the affected surface (`articles`, `videos`, or `reels`).
2. Check source health and recent ingestion activity.
3. Verify daily targets and freshness thresholds are realistic for the current source set.
4. Trigger manual fetch or cleanup if needed.

## Editorial and operator workflows

Current operator surfaces:

- API routes: `/api/v1/admin/*`
- HTML UI: `/api/v1/admin/ui/*`
- Editorial JSON API: `/api/v1/admin/editorial/*`

Examples:

```bash
# Editorial content list
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  "$HOST/api/v1/admin/editorial/content?page_size=10" | jq

# Manual URL submission
curl -s -X POST \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  "$HOST/api/v1/admin/editorial/content/submit" \
  -d '{"url":"https://example.com/article","importance_level":2}' | jq
```

## Release discipline

Use this checklist for backend changes:

1. Run local CI for the impacted scope from `src/backend/`.
2. Review deploy-affecting config changes in `render.yaml`, `.env.example`, and `docs/CONFIGURATION.md` together.
3. Push only deployable changes to `main`.
4. Watch the Render deploy and validate `/health` immediately after deploy.

## Cleanup discipline

When an endpoint, script, or operational path is removed:

1. Remove or update the related commands in this runbook.
2. Update `docs/CLEANUP_AUDIT.md` with the evidence and execution status.
3. Re-check the live route table if the change touched mounted routers.
