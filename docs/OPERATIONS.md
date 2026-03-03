# Operations Runbook

Comprehensive operational guide for Blips backend service.

**Last Updated:** February 2026  
**Service Owner:** Engineering Team

---

## Table of Contents

1. [Quick Reference](#quick-reference)
2. [Health Monitoring](#health-monitoring)
3. [Common Incidents](#common-incidents)
4. [Emergency Procedures](#emergency-procedures)
5. [Maintenance Tasks](#maintenance-tasks)
6. [Editorial Control](#editorial-control)

---

## Quick Reference

### Service URLs (Production)

| Service | URL |
|---------|-----|
| API | `https://blips-api.onrender.com` |
| Health Check | `GET /health` |
| Metrics | `GET /metrics` (requires ADMIN_API_KEY) |
| Ops Status | `GET /ops/status` (requires ADMIN_API_KEY) |

### Admin API Access

```bash
# Set your admin key
export ADMIN_KEY="your-admin-api-key"

# Health check (no auth required)
curl -s https://blips-api.onrender.com/health | jq

# Metrics (requires auth)
curl -s -H "X-Admin-Key: $ADMIN_KEY" https://blips-api.onrender.com/metrics | jq

# Operational status (requires auth)
curl -s -H "X-Admin-Key: $ADMIN_KEY" https://blips-api.onrender.com/ops/status | jq
```

### Key Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `DATABASE_URL` | PostgreSQL connection | Required |
| `REDIS_URL` | Redis connection | Required |
| `ADMIN_API_KEY` | Admin endpoint auth | Required |
| `OPENAI_API_KEY` | OpenAI API access | Optional |
| `MISTRAL_API_KEY` | Mistral API access | Required |
| `INGESTION_CRON_DISABLED` | Kill switch for ingestion | `false` |
| `LLM_DAILY_CEILING_USD` | Daily LLM spend limit | `5.0` |

---

## Health Monitoring

### Health Check Response

```json
{
  "status": "healthy",
  "database": "ok",
  "redis": "ok"
}
```

**Status Codes:**
- `200`: All systems operational
- `503`: One or more dependencies failing

### Ops Status Response

Key metrics to monitor:

```json
{
  "request_metrics": {
    "total_requests": 1234,
    "total_errors": 12,
    "error_rate": 0.0097,
    "uptime_seconds": 86400,
    "top_endpoints": [...],
    "slow_endpoints": [...]
  },
  "redis_pool": {
    "status": "ok",
    "max_connections": 20
  },
  "db_pool": {
    "status": "ok",
    "size": 5,
    "checkedout": 1
  }
}
```

### Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Error rate | > 1% | > 5% |
| Avg latency | > 500ms | > 2000ms |
| DB pool checked out | > 80% | > 95% |
| Tier A content | < 20 items | < 10 items |

---

## Common Incidents

### 1. Database Connection Failures

**Symptoms:**
- Health check returns `database: error`
- 503 responses on API calls
- Logs show `SQLAlchemyError`

**Investigation:**
```bash
# Check health
curl -s https://blips-api.onrender.com/health | jq

# Check Render database status
# Go to Render dashboard -> Database -> Logs
```

**Resolution:**
1. Check Render PostgreSQL dashboard for outages
2. Verify `DATABASE_URL` is correct in Render env vars
3. Check if database is over connection limit
4. Restart web service if needed

### 2. Redis Connection Failures

**Symptoms:**
- Health check returns `redis: error`
- Rate limiting not working
- Feed caching disabled

**Investigation:**
```bash
# Check health
curl -s https://blips-api.onrender.com/health | jq '.redis'
```

**Resolution:**
1. Check Render Redis dashboard
2. Verify `REDIS_URL` in environment
3. Redis may need restart if maxmemory exceeded

### 3. Ingestion Stopped

**Symptoms:**
- `/metrics` shows 0 items_ingested
- Tier A content count dropping
- `needs_topup: true` persisting

**Investigation:**
```bash
# Check metrics
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  https://blips-api.onrender.com/metrics | jq '.totals'

# Check scheduler state
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  https://blips-api.onrender.com/metrics | jq '.scheduler'
```

**Resolution:**
1. Check Render worker service logs for errors
2. Verify RSS feed URLs are accessible
3. Check if `INGESTION_CRON_DISABLED=true` was accidentally set
4. Restart worker service

### 4. LLM API Failures

**Symptoms:**
- AI chat returning 503 errors
- Summaries not being generated
- Logs show `LLMQuotaExceededError` or `LLMConfigurationError`

**Investigation:**
```bash
# Check LLM provider status pages:
# - Mistral: https://status.mistral.ai
# - OpenAI: https://status.openai.com

# Check daily spend (in logs)
grep "LLM daily spend" /var/log/app.log | tail -5
```

**Resolution:**
1. If quota exceeded: Wait until next day (UTC reset) or increase `LLM_DAILY_CEILING_USD`
2. If configuration error: Verify API keys in environment
3. If provider outage: Service will fallback to non-AI summaries

### 5. High Latency

**Symptoms:**
- `slow_endpoints` in /ops/status showing > 2s avg
- User complaints about slow app

**Investigation:**
```bash
# Check slow endpoints
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  https://blips-api.onrender.com/ops/status | jq '.request_metrics.slow_endpoints'

# Check DB pool
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  https://blips-api.onrender.com/ops/status | jq '.db_pool'
```

**Resolution:**
1. If DB pool exhausted: Restart service to clear pool
2. If specific endpoint slow: Check N+1 queries in that endpoint
3. Scale up Render plan if persistent

---

## Emergency Procedures

### Kill Switch: Stop All Ingestion

```bash
# Set environment variable in Render dashboard:
INGESTION_CRON_DISABLED=true

# Then restart the worker service
```

### Force Content Cache Clear

```bash
# Connect to Redis and flush tiered feed cache
redis-cli -u $REDIS_URL
> KEYS "blips:tiered_feed:*"
> DEL "blips:tiered_feed:articles" "blips:tiered_feed:videos" "blips:tiered_feed:reels"
```

### Rollback Deployment

1. Go to Render dashboard -> Service -> Deploys
2. Find the previous successful deploy
3. Click "Redeploy"

### Database Migration Rollback

```bash
# If a migration failed, rollback via:
cd src/backend
alembic downgrade -1
```

---

## Maintenance Tasks

### Weekly

- [ ] Review error rates in `/ops/status`
- [ ] Check LLM spending trends
- [ ] Verify all RSS/YouTube sources are fetching

### Monthly

- [ ] Review slow endpoints, optimize if needed
- [ ] Check database size and cleanup old data if needed
- [ ] Update dependencies (security patches)

### Quarterly

- [ ] Review and update this runbook
- [ ] Load test with expected growth
- [ ] Security review

---

## Contacts

| Role | Contact |
|------|---------|
| Service Owner | @engineering-team |
| Render Support | support@render.com |
| Mistral Support | support@mistral.ai |

---

## Appendix: Useful Commands

```bash
# View recent logs (Render CLI)
render logs --service blips-api --tail 100

# Check RSS feed health
curl -s "https://techcrunch.com/feed/" | head -20

# Test YouTube RSS
curl -s "https://www.youtube.com/feeds/videos.xml?channel_id=UCBcRF18a7Qf58cCRy5xuWwQ" | head -20

# Validate Redis connection
redis-cli -u $REDIS_URL PING
```

---

## Database Failover

### Prevention
- `pool_pre_ping=True`: SQLAlchemy pings each connection before reuse, transparently discarding stale ones
- `pool_recycle=1800`: Connections recycled every 30 minutes to prevent cloud DB idle disconnects
- Pool: 5 base + 10 overflow = max 15 concurrent connections

### Detection
- `/health` endpoint runs `SELECT 1` — returns 503 if DB is unreachable
- Ingestion health check (`tasks_health.py`) monitors for stale data
- Alerting webhook fires on consecutive health check failures

### Recovery Procedure
1. **Check DB status**: `SELECT 1` via psql or Render dashboard
2. **If Render DB is down**: Check Render status page; DB auto-recovers on their infra
3. **If connection pool exhausted**: Restart the web service (Render dashboard → Manual Deploy)
4. **If persistent**: Check `engine.pool.status()` via `/metrics` endpoint for pool stats
5. **Verify recovery**: Hit `/health` — should return 200 with `"database": "ok"`

### Connection Pool Monitoring
The `/metrics` endpoint exposes pool statistics:
- `pool_size`: Current pool capacity
- `checked_out`: Connections currently in use
- `overflow`: Extra connections beyond pool_size
- `checked_in`: Idle connections in pool

---

## Incident Response Runbooks

### INC-1: Complete Service Outage (Health Returns 503)

**Severity:** P1 — Immediate  
**Detection:** `/health` returns 503, alerting webhook fires  

**Triage (first 5 minutes):**
1. Check Render dashboard: https://dashboard.render.com
2. Is the service running? Check deploy logs for crash loops
3. Run: `curl -s https://blips-api.onrender.com/health | jq`
4. Check which dependency failed (database, redis, or both)

**If Database is down:**
1. Check Render PostgreSQL dashboard for status
2. Try `SELECT 1` via psql if you have direct access
3. If Render outage → check https://status.render.com, wait for recovery
4. If connection pool exhausted → restart: Render dashboard → Manual Deploy
5. Verify: `/health` returns `"database": "ok"`

**If Redis is down:**
1. Check Render Redis dashboard for status
2. Redis is used for rate limiting and caching — app should partially work
3. Note: `redis_health_guard` middleware returns 503 when Redis is down
4. If Redis is fully gone → restart service to reconnect
5. Verify: `/health` returns `"redis": "ok"`

**If service itself crashed:**
1. Check Render deploy logs for Python tracebacks
2. Common causes: missing env var, bad migration, OOM
3. Roll back: Render dashboard → Deploys → Revert to last working deploy
4. Verify: `/health` returns 200

**Post-incident:**
- Write incident report within 24h
- Update this runbook if new failure mode discovered

---

### INC-2: Ingestion Stall (No New Content)

**Severity:** P2 — High (2h+ to detect)  
**Detection:** `check_ingestion_health` job fires alert after 2h of no insertions  

**Triage:**
1. Check `/metrics` for `ingestion_health.is_stalled`
2. Check scheduler status: is `fetch_news` job running?
3. Review logs for ingestion errors: `INGESTION_CRON_DISABLED` set to `true`?

**If scheduler stopped:**
1. Check if `SCHEDULER_ENABLED=true` in env
2. Restart service: Render dashboard → Manual Deploy
3. Verify: Check logs for "fetch_and_process_news completed"

**If feeds are failing:**
1. Check `/metrics/sources` for `problem_feeds`
2. Check if specific RSS feeds changed URLs
3. Check if YouTube API key is exhausted (quota resets at midnight PT)
4. Verify: `articles_ingested_last_2h > 0` in `/metrics`

**If LLM quota exceeded:**
1. Check `/metrics` for LLM spend
2. If `LLM_DAILY_CEILING_USD` is hit → articles ingest but without AI summaries
3. Summaries will be retried by `retry_ai_processing` job
4. Optional: increase `LLM_DAILY_CEILING_USD` temporarily

---

### INC-3: High Error Rate on Mobile

**Severity:** P2 — High  
**Detection:** Crash reports, user feedback, or API error spike  

**Triage:**
1. Check error codes in API logs — are they 4xx or 5xx?
2. Is this affecting all users or specific endpoints?
3. Check circuit breaker state: is YouTube breaker OPEN?

**If API returning 5xx:**
→ Follow INC-1 runbook

**If API returning 429 (rate limited):**
1. Check if a single device is hammering the API
2. Review `RATE_LIMIT_DEFAULT` (60/min) and `RATE_LIMIT_CHAT` (10/min)
3. If legitimate traffic spike → temporarily increase limits

**If YouTube content broken:**
1. Check circuit breaker stats in logs
2. If breaker is OPEN → YouTube API is down, will auto-recover in 60s
3. If persistent → check YouTube Data API quota in Google Cloud Console
4. Shorts detection and duration fetching degrade gracefully (return defaults)

---

### INC-4: LLM Service Degradation

**Severity:** P3 — Medium  
**Detection:** `LLMQuotaExceededError` or `LLMConfigurationError` in logs  

**Impact:** AI chat and summaries unavailable, feed still works  

**Resolution:**
1. Check `LLM_DAILY_COST_CEILING` vs actual spend
2. If quota hit legitimately → wait for midnight reset
3. If API key expired → rotate key in Render env vars
4. If provider outage → check https://status.openai.com or https://status.mistral.ai
5. `retry_ai_processing` job will automatically retry failed summaries

---

## Editorial Control

Admin portal for content curation. Full docs: [`src/backend/docs/EDITORIAL_CONTROL.md`](../src/backend/docs/EDITORIAL_CONTROL.md).

### Quick Commands

```bash
export ADMIN_KEY="your-admin-api-key"
export HOST="https://blips-api.onrender.com"

# List content (filtered by day)
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  "$HOST/admin/editorial/content?day=2024-01-15&page_size=10" | jq

# Submit a URL for ingestion
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  "$HOST/admin/editorial/content/submit" \
  -d '{"url": "https://example.com/article", "importance_level": 2}' | jq

# Boost a content item (0-3)
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  "$HOST/admin/editorial/content/42/boost" \
  -d '{"level": 2}' | jq

# Suppress (soft delete) content from feeds
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" \
  "$HOST/admin/editorial/content/42/suppress" | jq

# Unsuppress
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" \
  "$HOST/admin/editorial/content/42/unsuppress" | jq

# View content detail + audit trail
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  "$HOST/admin/editorial/content/42" | jq
```

### Admin UI

Browser-based dashboard at `$HOST/admin/ui/?key=$ADMIN_KEY`.

### Migration

```bash
cd src/backend && alembic upgrade head
```

---

## Environment Variable Reference

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

---

## Release Checklist

Use this checklist before each release to production.

## Pre-Release

### Code Quality
- [ ] All tests pass locally
- [ ] No linting errors (`ruff check .`)
- [ ] Code reviewed and approved
- [ ] Feature branch merged to `develop`

### Testing in Dev
- [ ] Deployed to dev environment
- [ ] Manual smoke test passed
- [ ] API endpoints respond correctly
- [ ] Background jobs running
- [ ] No errors in logs

### Database
- [ ] Migrations are additive only (no drops)
- [ ] Migrations tested in dev
- [ ] Backward compatible with current code
- [ ] No breaking schema changes

### Documentation
- [ ] README updated if needed
- [ ] API changes documented
- [ ] CHANGELOG updated

## Release

### Deploy to Production
- [ ] Create PR from `develop` to `main`
- [ ] PR reviewed and approved
- [ ] Merge PR (triggers auto-deploy)
- [ ] Monitor Render deploy logs

### Verify
- [ ] Health endpoint returns 200
- [ ] API endpoints respond correctly
- [ ] Background jobs running
- [ ] Mobile app connects successfully
- [ ] No errors in logs

## Post-Release

### Monitor (first 30 min)
- [ ] Error rate normal
- [ ] Response times normal
- [ ] Memory/CPU usage normal
- [ ] No user-reported issues

### Document
- [ ] Tag release in git (if major)
- [ ] Update CHANGELOG
- [ ] Notify team of successful release

## Rollback Triggers

Initiate rollback if:
- Error rate > 5%
- Response time > 2s (p95)
- Any data corruption
- Critical functionality broken

## Rollback Steps

1. Go to Render Dashboard → Service → Events
2. Find last working deploy
3. Click "Rollback to this deploy"
4. If DB migration: `alembic downgrade -1`
5. Verify rollback successful
6. Investigate and fix issue
7. Re-release when ready

## Hotfix Process

For critical production issues:

1. Create branch from `main`: `hotfix/issue-name`
2. Fix the issue
3. Test locally
4. PR directly to `main` (bypass develop)
5. After merge, cherry-pick to `develop`

---

## Scaling

### Web Service

```yaml
# render.yaml
scaling:
  minInstances: 1
  maxInstances: 3
  targetMemoryPercent: 80
  targetCPUPercent: 80
```

### Worker

- Single instance (scheduler uses single-leader pattern)
- Scale by increasing job parallelism in code

## Costs (Estimated)

| Service | Plan | Monthly |
|---------|------|---------|
| Web API | Starter | $7 |
| Worker | Starter | $7 |
| Postgres | Starter | $7 |
| Redis | Starter | $10 |
| **Total** | | **~$31** |

## Troubleshooting
## Costs (Estimated)

| Service | Plan | Monthly |
|---------|------|---------|
| Web API | Starter | $7 |
| Worker | Starter | $7 |
| Postgres | Starter | $7 |
| Redis | Starter | $10 |
| **Total** | | **~$31** |

## Troubleshooting

---

## Inventory Health Runbook

## Health Check

```bash
# Full health status
curl -s http://localhost:8000/inventory/health | jq

# Single surface
curl -s http://localhost:8000/inventory/health/articles | jq
```

## Key Metrics

| Metric | Healthy | Warning | Action |
|--------|---------|---------|--------|
| `tier_a` (articles) | > 30 | 10-30 | Monitor |
| `tier_a` (articles) | - | < 10 | Check RSS feeds |
| `needs_topup` | false | true | Auto-handled |
| `below_min_fresh` | false | true | Check ingestion |

## Quick Fixes

### Force Ingestion
```bash
# Trigger immediate RSS fetch
curl -X POST http://localhost:8000/admin/ingest/rss

# Trigger YouTube fetch
curl -X POST http://localhost:8000/admin/ingest/youtube
```

### Check Feed Sources
```bash
# List all sources with last fetch time
curl -s http://localhost:8000/sources | jq '.[] | {name, last_fetch, is_active}'
```

### Clear Feed Cache
```bash
# Via Redis CLI
redis-cli KEYS "blips:tiered_feed:*" | xargs redis-cli DEL
```

## Thresholds (Production Defaults)

```
Articles: min_fresh=30, reservoir=200
Videos:   min_fresh=25, reservoir=150  
Reels:    min_fresh=20, reservoir=300
```

Adjust via environment variables:
```bash
export MIN_FRESH_ARTICLES=50  # More aggressive threshold
export RESERVOIR_ARTICLES=300 # Larger pool
```

## Logs to Watch

```bash
# Top-up activity
grep "Top-up" /var/log/blips/app.log

# Tier distribution
grep "Tiered feed for" /var/log/blips/app.log

# Cache hits/misses
grep "Cache HIT\|Cache MISS" /var/log/blips/app.log
```

## Alerts

Set up alerts for:
1. `needs_topup=true` persisting > 10 minutes
2. `tier_a` count < 10 for any surface
3. Top-up failing with errors

## Recovery Procedures

### Empty Feed
1. Check `/inventory/health` - identify which tier is empty
2. Check `/sources` - verify feeds are active
3. Trigger manual ingest if needed
4. Monitor tier counts recovering

### Stuck Top-Up
1. Check Redis for lock: `redis-cli GET blips:topup_lock`
2. If stale (> 5 min), delete: `redis-cli DEL blips:topup_lock`
3. Top-up will restart on next API request

### High Tier C Ratio
This is normal during slow news periods. The system is working correctly by backfilling with quality content.
