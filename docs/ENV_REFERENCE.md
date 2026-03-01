# Environment Variable Reference

All configuration is managed via environment variables loaded by
`pydantic-settings` in `app/core/config.py`.  Set values in the Render
dashboard or in a local `.env` file.

---

## Core / Identity

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ENV` | `dev` | No | `dev` or `prod` — controls default behaviours |
| `LOG_LEVEL` | `INFO` | No | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PYTHON_VERSION` | — | Render | Render-specific; set to `3.11.4` |

## Security

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ADMIN_API_KEY` | `""` | **Yes (prod)** | Protects `/admin/*`, `/metrics`, `/ops/status` |
| `CORS_ORIGINS` | `""` | No | Comma-separated allowed origins. Defaults to `capacitor://localhost,http://localhost` |
| `DOCS_ENABLED` | `false` | No | Expose Swagger UI at `/docs` and ReDoc at `/redoc` |
| `DEBUG_ROUTES_ENABLED` | `false` | No | Expose debug endpoints |

## Database (PostgreSQL)

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DATABASE_URL` | `postgresql://…db:5432/blips` | **Yes** | Full Postgres connection string |
| `DB_POOL_SIZE` | `3` | No | SQLAlchemy pool core size per worker |
| `DB_MAX_OVERFLOW` | `5` | No | Extra connections above pool_size |
| `DB_POOL_TIMEOUT` | `30` | No | Seconds to wait for a connection |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | No | Recycle connections after N seconds |

> **Render Basic-256MB Postgres** allows ~97 connections.  With 2 Gunicorn
> workers the default (`pool_size=3 + max_overflow=5` = 8 per worker × 2 = 16)
> plus the worker service (8) totals 24 — safely within the limit.

## Redis

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `REDIS_URL` | `redis://redis:6379/0` | **Yes** | Full Redis connection string |
| `REDIS_MAX_CONNECTIONS` | `20` | No | Max connections in the shared pool |

## LLM / AI

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LLM_PROVIDER` | `openai` | No | `openai`, `mistral`, or `fake` (test) |
| `OPENAI_API_KEY` | `""` | If provider=openai | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | No | OpenAI model name |
| `MISTRAL_API_KEY` | `""` | If provider=mistral | Mistral API key |
| `MISTRAL_MODEL` | `mistral-small-latest` | No | Mistral model name |
| `LLM_REQUEST_TIMEOUT` | `30` | No | Seconds per LLM API call |
| `LLM_DAILY_COST_CEILING` | `5.0` | No | Max estimated daily LLM spend (USD). `0` = unlimited |

## Scheduler

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SCHEDULER_ENABLED` | `true` | No | `false` on the web service; `true` on the worker |
| `NEWS_FETCH_INTERVAL_MINUTES` | `30` | No | How often to run ingestion |
| `SCHEDULER_LOCK_TTL_SECONDS` | `120` | No | Redis leader-lock TTL (env var, not pydantic) |
| `SCHEDULER_LOCK_REFRESH_SECONDS` | `30` | No | Lock refresh interval (env var) |

## Ingestion

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `INGESTION_ENABLED` | `true` | No | Master switch for ingestion |
| `INGESTION_CRON_DISABLED` | `false` | No | Disable scheduled cron runs |
| `INGESTION_MAX_WORKERS` | `1` | No | Parallel ingestion workers |
| `MAX_ITEMS_PER_RUN` | — | No | Cap items processed per scheduler run |
| `INGEST_UNTIL_TARGETS` | `true` | No | Keep ingesting until daily targets met |
| `INGEST_CATCHUP_MAX_SECONDS` | `1800` | No | Max seconds for catch-up loop |
| `INGESTION_POLL_SECONDS` | `30` | No | Seconds between poll cycles |
| `INGESTION_LEASE_TTL_MS` | `60000` | No | Feed-level lease TTL (ms) |
| `RSS_ENTRIES_PER_FEED` | `50` | No | Max entries to fetch from each RSS feed |
| `YT_VIDEOS_PER_CHANNEL` | `30` | No | Max videos to fetch per YouTube channel |
| `YOUTUBE_API_KEY` | — | **Yes** | YouTube Data API v3 key |

## Feature Flags (Redis-backed)

These flags are managed at runtime via the admin API (`PUT /admin/flags/{name}`).
The env-var equivalents serve as fallback defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_SUMMARIZATION_ENABLED` | `true` | Enable LLM summarization |
| `FEATURE_INGESTION_ENABLED` | `true` | Enable content ingestion |

## Caching / TTL

| Variable | Default | Description |
|----------|---------|-------------|
| `FEED_CACHE_TTL_SECONDS` | `300` | Feed/playlist cache TTL |
| `ITEM_CACHE_TTL_SECONDS` | `3600` | Per-item cache TTL |
| `CONFIG_CACHE_TTL_SECONDS` | `21600` | Feature flag Redis TTL (6 h) |
| `LEASE_TTL_SECONDS` | `300` | Distributed lock / lease TTL |

## Data Retention

| Variable | Default | Description |
|----------|---------|-------------|
| `RETAIN_CONTENT_DAYS` | `90` | content_items older than N days deleted |
| `RETAIN_INGESTION_PROGRESS_DAYS` | `14` | ingestion_progress rows |
| `RETAIN_EVENTS_DAYS` | `30` | interaction_events rows |
| `RETAIN_CONVERSATIONS_DAYS` | `30` | conversations rows |
| `RETAIN_USAGE_DAYS` | `90` | usage rows |
| `RETAIN_EDITORIAL_DAYS` | `180` | editorial_actions rows |
| `RETAIN_DEBUG_DAYS` | `7` | Debug / ephemeral data |

## Alerting

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_ENABLED` | `false` | Enable webhook alerts |
| `ALERT_WEBHOOK_URL` | `""` | Slack / Discord webhook URL |

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_DEFAULT` | `60/minute` | Default per-IP rate limit |
| `RATE_LIMIT_CHAT` | `10/minute` | Chat endpoint rate limit |

## Ads (architecture only — all OFF)

| Variable | Default | Description |
|----------|---------|-------------|
| `ADS_ENABLED` | `false` | Master ads switch |
| `ADS_FEED_CARD_ENABLED` | `false` | In-feed ad cards |
| `ADS_BANNER_ENABLED` | `false` | Banner ads |
| `ADS_FEED_FREQUENCY` | `0` | 1 ad per N organic items (`0` = off) |
| `ADS_CANARY_PERCENT` | `0` | % of requests receiving ads |

---

## Render Blueprint Architecture

The Blueprint (`render.yaml`) uses Render's `projects` + `environments` feature
to deploy **two environments** from a single file:

| Environment | Services | Description |
|-------------|----------|-------------|
| **development** | `blips-dev-api` (web) + `blips-dev-db` + `blips-dev-redis` | Single service, `SCHEDULER_ENABLED=true`, Swagger enabled |
| **production** | `blips-api` (web) + `blips-worker` (background) + `blips-db` + `blips-redis` | API + dedicated worker, scheduler on worker only |

Shared secrets (`ADMIN_API_KEY`, API keys, `LLM_PROVIDER`) are defined as
`sync: false` on each service — set them per-service in the Render dashboard.

> **Note:** `sync: false` cannot be used inside `envVarGroups` (Render ignores it),
> so secrets must be set individually on each service.

### Secrets to set in dashboard after Blueprint deploy

Set on **each service** (dev-api, prod-api, prod-worker):
```
ADMIN_API_KEY=<strong-random-secret>
OPENAI_API_KEY=<key>
MISTRAL_API_KEY=<key>
YOUTUBE_API_KEY=<key>
LLM_PROVIDER=mistral
```

Production services only:
```
ENV=prod
LOG_LEVEL=WARNING
```
