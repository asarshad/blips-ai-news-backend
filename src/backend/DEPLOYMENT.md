# Deployment Guide

This document explains how to deploy Blips backend to Render.

## Environments

| Environment | Branch | URL | Purpose |
|-------------|--------|-----|---------|
| **dev** | `develop` | `blips-dev.onrender.com` | Testing, staging |
| **prod** | `main` | `blips.onrender.com` | Production |

## Branch → Environment Mapping

```
main     → prod (automatic deploy on push)
develop  → dev  (automatic deploy on push)
```

### Workflow

1. Create feature branches from `develop`
2. PR to `develop` → deploys to dev
3. Test in dev environment
4. PR from `develop` to `main` → deploys to prod

## Architecture on Render

```
┌─────────────────────────────────────────────────────────────┐
│                         Render                               │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │   Web API   │────▶│  Postgres   │◀────│   Worker    │   │
│  │  (FastAPI)  │     │  (Managed)  │     │ (Scheduler) │   │
│  └──────┬──────┘     └─────────────┘     └──────┬──────┘   │
│         │                                        │          │
│         └──────────────┬─────────────────────────┘          │
│                        ▼                                    │
│                 ┌─────────────┐                             │
│                 │    Redis    │                             │
│                 │  (Managed)  │                             │
│                 └─────────────┘                             │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Services

| Service | Type | Scaling | Purpose |
|---------|------|---------|---------|
| Web API | Web Service | Auto-scale | REST API, handles requests |
| Worker | Background Worker | 1 instance | Scheduler, ingestion jobs |
| Postgres | Managed DB | Starter | Primary database |
| Redis | Managed Cache | Starter | Caching, job queues |

## Environment Variables

Set these in Render Dashboard → Service → Environment:

### Required for All Environments

```bash
DATABASE_URL          # Auto-set by Render Postgres
REDIS_URL             # Auto-set by Render Redis
OPENAI_API_KEY        # Your OpenAI API key
ENV                   # "dev" or "prod"
```

### Optional (with defaults)

```bash
LOG_LEVEL             # INFO (dev), WARNING (prod)
NEWS_FETCH_INTERVAL_MINUTES=30
MAX_ITEMS_PER_RUN=100
PREFERENCE_DECAY_FACTOR=0.95
```

## Deploy Process

### Initial Setup

1. **Create Render services** (see `render.yaml`)
2. **Set environment variables** in Render Dashboard
3. **Deploy** by pushing to the appropriate branch

### Automatic Deploys

Render auto-deploys on push to:
- `main` → prod
- `develop` → dev

### Manual Deploy

```bash
# From Render Dashboard
# Services → Select service → Manual Deploy → Deploy latest commit
```

## Database Migrations

Migrations run automatically on deploy via the release command in `render.yaml`.

### Manual Migration

```bash
# SSH into web service or use Render Shell
alembic upgrade head
```

### Migration Rules

1. **Additive only** - never drop columns/tables in prod
2. **Backward compatible** - old code must work with new schema
3. **Test in dev first** - always deploy to dev before prod

## Rollback Strategy

### Quick Rollback (< 5 min)

1. Go to Render Dashboard → Service → Events
2. Find last working deploy
3. Click "Rollback to this deploy"

### Code Rollback

```bash
# Revert the commit
git revert HEAD
git push origin main  # or develop

# Or reset to specific commit (force push required)
git reset --hard <commit-sha>
git push --force origin main
```

### Database Rollback

⚠️ **CAUTION**: Only for schema changes, not data

```bash
# Check current revision
alembic current

# Downgrade one step
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade <revision>
```

### Full Rollback Procedure

1. **Stop traffic** - Scale web to 0 instances
2. **Rollback code** - Use Render rollback or git revert
3. **Rollback database** (if needed) - `alembic downgrade -1`
4. **Restore traffic** - Scale web back up
5. **Verify** - Check health endpoint and logs

## Health Checks

### Endpoints

- `GET /health` - Basic health check
- `GET /api/v1/health` - API health with DB/Redis status

### Monitoring

Render provides:
- Request logs
- Metrics (CPU, memory)
- Alerts

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

### Service Won't Start

1. Check Render logs for error
2. Verify all env vars are set
3. Check DATABASE_URL format
4. Ensure migrations ran

### Migrations Failed

1. Check Alembic revision history
2. Look for conflicting migrations
3. Test migration locally first

### High Latency

1. Check database connection pooling
2. Review Redis cache hit rate
3. Check for N+1 queries
4. Scale up if CPU/memory bound

## Security Checklist

- [ ] All secrets in Render env vars (not in code)
- [ ] DATABASE_URL uses SSL (`?sslmode=require`)
- [ ] No debug mode in prod (`ENV=prod`)
- [ ] Rate limiting enabled
- [ ] CORS configured for allowed origins
