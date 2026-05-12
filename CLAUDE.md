# Blips Backend — Agent Context

## What Is This?

FastAPI backend for Blips — automated tech news ingestion, AI processing, ranking, and API for the mobile app.  
**Push to `main` = immediate auto-deploy to Render. Treat every commit as production.**

Production API: `https://api.blips.tech/api/v1`  
Admin UI: `https://blips-api.onrender.com/api/v1/admin/ui/`

---

## Non-Negotiable Agent Rules

Before any push to `main`, from `src/backend/`:

```bash
./.venv/bin/python -m ruff check app tests
./.venv/bin/python -m ruff format --check app tests
./.venv/bin/python -m pytest <impacted scope>
```

No exceptions. Render deploys on commit — there is no staging gate.

---

## Production Topology (Render, region: oregon)

```
blips-api     srv-d6hvve8gjchc73d0cq7g   Web service (standard)    SCHEDULER_ENABLED=false
blips-worker  srv-d6hvve8gjchc73d0cq70   Worker (standard)         Runs 3 lanes
blips-db      dpg-d6hvtchdrdic73ct8fe0-a PostgreSQL basic-1g
blips-redis   red-d6hvv2ogjchc73d0ckv0   Redis starter
```

The **API never runs background jobs**. The **worker owns all ingestion and scheduling**.  
Both share the same DB and Redis instance.

```bash
# Render CLI quick commands
render logs srv-d6hvve8gjchc73d0cq7g          # API logs
render logs srv-d6hvve8gjchc73d0cq70          # Worker logs
render deploys list srv-d6hvve8gjchc73d0cq7g  # Deploy status
render psql dpg-d6hvtchdrdic73ct8fe0-a --command "SELECT ..." -o text
```

---

## Admin API Access

```bash
export HOST="https://blips-api.onrender.com"
export ADMIN_KEY="<see Render env vars>"
```

Key endpoints:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness |
| `GET /metrics` | X-Admin-Key | Service metrics |
| `GET /ops/status` | X-Admin-Key | DB + Redis snapshot |
| `GET /api/v1/inventory/health` | X-Admin-Key | Content surface health |
| `GET /api/v1/metrics/sources` | X-Admin-Key | Feed ingestion health |
| `POST /api/v1/admin/trigger-fetch` | X-Admin-Key | Manual ingestion trigger |
| `POST /api/v1/admin/trigger-summarize` | X-Admin-Key | Manual AI retry |
| `POST /api/v1/admin/trigger-scripted-maintenance` | X-Admin-Key | One-off backfill / repair jobs (see below) |
| `GET /api/v1/admin/ui/` | browser login | Operator dashboard |

### One-off backfill / repair jobs (`trigger-scripted-maintenance`)

All one-off data repairs run through a single endpoint backed by `scripts/operator_backfill_job.py`. Add new jobs there — **do not create standalone scripts that require a local DATABASE_URL**.

```bash
curl -s -X POST https://blips-api.onrender.com/api/v1/admin/trigger-scripted-maintenance \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"payload": {"job": "<job_name>", ...options}}'
```

| Job name | Purpose | Key options |
|---|---|---|
| `article_image_backfill` *(default)* | Re-verify missing article images | `lookback_days`, `limit`, `all_statuses` |
| `article_quality_gate_backfill` | Re-run quality classifier on recent unsuppressed items | `lookback_days`, `limit`, `dry_run` |
| `major_news_stale_probe_cleanup` | Clear stale `is_major_tech_news=true` flags | `max_age_hours`, `limit`, `dry_run` |
| `content_event_backfill` | Re-enqueue pending content events | `lookback_days`, `limit`, `pending_only` |
| `repair_unskimmable_with_description` | Un-suppress paywalled articles that have usable RSS descriptions | `source`, `dry_run`, `limit`, `min_desc_words`, `lookback_days` |

To add a new job: implement `_run_<job_name>(db, options)` in `scripts/operator_backfill_job.py` and register it in the `run()` dispatcher.

---

## Architecture in One Page

```
RSS/YouTube → ingestion_lane → content_items (PENDING)
                                     ↓ promotion_service (5 min)
                                content_items (PROMOTED)
                                     ↓ content_event_lane
                                content_ai_service
                                  • extraction/pipeline.py
                                  • LLM tech classifier
                                  • LLM summarisation (gpt-5-mini)
                                  • article_image_service
                                     ↓ content_readiness.py
                                content_items (READY)
                                     ↓
                                playlist_service.py → mobile app
```

**3 Worker Lanes** (all inside one container, thread-based):
- `ingestion_lane` — RSS + YouTube → DB
- `content_event_lane` — drains outbox, fires AI pipeline
- `maintenance_lane` — periodic tasks (scoring 60min, cleanup daily, etc.)

---

## Key Docs Map

| What you need | Where to look |
|---|---|
| Full architecture diagram | `docs/ARCHITECTURE.md` |
| All config env vars | `docs/CONFIGURATION.md` |
| Operations runbook + curl commands | `docs/OPERATIONS.md` |
| Ingestion system detail | `docs/INGESTION.md` |
| Content pipeline (RSS) | `src/backend/docs/RSS_INGESTION.md` |
| Video ingestion | `src/backend/docs/VIDEO_INGESTION.md` |
| Worker lanes | `src/backend/docs/WORKER_LANES.md` |
| Curation + ranking | `src/backend/docs/CURATION_SYSTEM.md` |
| Testing | `src/backend/docs/TESTING.md` |
| Alerting + dashboards | `src/backend/docs/OPS_DASHBOARDS_ALERTS.md` |
| Retention runbook | `src/backend/docs/RETENTION_RUNBOOK.md` |
| Hosting runbook (Render) | `docs/LAUNCH_HOSTING_RUNBOOK.md` |
| Repo-local skills | `.skills/` (backend-inspector, article-image-edgecase-sweeper) |

---

## Project Structure (src/backend/)

```
app/
├── main.py                          # FastAPI app factory
├── core/config.py                   # All settings (Pydantic BaseSettings)
├── workers/launcher.py              # Worker entry point (3 lanes)
├── workers/maintenance_lane.py      # Scheduled task registry
├── ingestion/checkpoint_worker.py   # RSS → DB upsert
├── services/content_ai_service.py   # AI pipeline orchestrator
├── services/promotion_service.py    # PENDING → PROMOTED
├── services/playlist_service.py     # Feed generation (~1500 lines)
├── services/inventory_service.py    # Surface health
├── services/alerting_service.py     # Discord alerts
├── integrations/rss_feeds.py        # ~70 feed configs
├── integrations/youtube_client.py   # YouTube Data API
├── integrations/llm_client.py       # OpenAI/Mistral unified client
├── extraction/pipeline.py           # Article fetch + HTML extract
└── api/admin/ui.py                  # Server-rendered admin panel
```

---

## CI / GitHub Actions

All workflows are in `.github/workflows/` and currently **disabled** (`.yml.disabled`) — intentionally, to avoid GitHub Actions billing costs. **Do not re-enable without confirming paid minutes are available.**

| Workflow | Trigger | What it does |
|---|---|---|
| `backend_pr.yml` | PR + push to `main` on `src/backend/**` | `ruff format`, `ruff check`, unit tests, QA gates (≥50% coverage) |
| `backend_integration.yml` | Daily 06:00 UTC + manual | Full integration tests with `--force-enable-socket` |
| `backend_operational_smoke.yml` | Every 45 min + manual | Probes live `https://api.blips.tech` — health, admin endpoints, inventory |
| `backend_render_runtime_monitor.yml` | Every 15 min + manual | Checks Render worker for runtime anomalies via Render API |
| `backend_security.yml` | PR + push to `main` | Gitleaks secret scan + dependency vulnerability review |

**Required GitHub secrets** (for operational workflows):
- `ADMIN_API_KEY` — backend admin key
- `ALERT_WEBHOOK_URL` — Discord webhook for alerts
- `RENDER_API_TOKEN` — Render API token for runtime monitor


---

## Database

Central table: `content_items` — all articles, videos, and reels.

Key status fields: `curation_status` (PENDING/PROMOTED/REJECTED/SUPPRESSED), `readiness_status` (PENDING/READY), `ai_processed`, `article_image_status`.

Migrations: Alembic. Always run `alembic upgrade head` after pulling schema changes.  
**DB enum values are UPPERCASE** — use `'ARTICLE'`, `'PROMOTED'`, etc. in raw SQL.

---

## Local Dev Setup

```bash
cd src/backend
source .venv/bin/activate          # or ./.venv/bin/python for one-off commands
cp .env.example .env               # fill in keys
alembic upgrade head
uvicorn app.main:app --reload      # requires Postgres + Redis running locally
```

Docker Compose also works: `docker compose -f src/docker-compose.yml up -d`

---

## LLM Models (pinned)

- Article summary: `gpt-5-mini` → `gpt-5.4-mini` (fallback) → `gpt-5.4` (rescue)
- Tech classifier: `gpt-5-mini`
- Daily cost ceiling: `LLM_DAILY_COST_CEILING=5.0` USD

LLM provider switchable via `LLM_PROVIDER=openai|mistral`.

---

## Known Issues

1. **Phase B ingestion refactor NOT started** — `IngestionBudget` table uses daily-bucket model. Plan documented in `docs/CONTINUOUS_7DAY_INGESTION_PLAN.md` but not yet implemented.
2. **Timezone mismatch in metrics** — supply metrics mix UTC and Toronto time. See BLIPS_CODEMAP.md for detail.
3. **`article_unskimmable_retry` backlog** — ~1500 items is normal steady-state, not a bug.
4. **YouTube quota pressure** — 10k units/day Data API v3 limit. Managed by `quota_manager.py`.

---

## Alerting

Discord webhook via `alerting_service.py`. Rate-limited 1 alert/key/5 min.  
Key alerts: ingestion stalled, low inventory, LLM cost ceiling hit, health check failed.
