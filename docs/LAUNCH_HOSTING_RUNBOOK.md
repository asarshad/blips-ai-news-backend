# Blips Launch Hosting Runbook

## Goal

Launch `blips` on Render without panic, learn from real traffic, and only move to Hetzner if the data says it is worth the extra ops burden.

Date captured: March 25, 2026

## Current Production Topology

Defined in [`render.yaml`](../render.yaml):

- `blips-api` on Render `starter`
- `blips-worker` on Render `starter`
- `blips-db` on Render `basic-256mb`
- `blips-redis` on Render `starter`

Relevant code paths:

- Scheduler jobs run frequently in [`src/backend/app/scheduler/__init__.py`](../src/backend/app/scheduler/__init__.py)
- Worker leader lock depends on Redis in [`src/backend/app/worker.py`](../src/backend/app/worker.py)
- Health and metrics endpoints live in [`src/backend/app/main.py`](../src/backend/app/main.py)

## Recommendation

Do not move API and worker to Hetzner before launch.

Launch on Render first, monitor real usage, then scale the component that actually becomes the bottleneck. For this app, the likely first bottleneck is the worker, not the API host itself.

## Why

- The scheduler runs jobs every 1 to 60 minutes, so the worker is truly always-on.
- The worker uses a Redis leader lock and refreshes it every minute, so stability of Redis matters.
- The API is separate from the scheduler already, which is the right shape for Render.
- Before launch, the bigger unknown is traffic shape and ingestion pressure, not whether Render can host a Python app.

## What To Monitor

Use these three sources first:

1. Render metrics for:
   - CPU
   - memory
   - restarts
   - response time
2. `GET /health`
3. `GET /metrics` and `GET /api/v1/metrics/inventory/health`

Useful endpoints:

- `GET /health`
- `GET /metrics` with `ADMIN_API_KEY`
- `GET /api/v1/metrics/inventory/health` with `ADMIN_API_KEY`
- `GET /api/v1/session/playlist-stats`

## Launch Checks

Before launch:

- Confirm `blips-api` and `blips-worker` both deploy cleanly.
- Confirm `/health` returns healthy.
- Confirm `/metrics` returns ingestion data and scheduler snapshot.
- Confirm inventory health has enough promoted content for the last 24 hours.
- Confirm the worker is continuously ingesting and not falling behind.
- Confirm LLM daily spend ceiling is set appropriately.

For the current Render setup, the main knobs are in [`render.yaml`](../render.yaml):

- `DB_POOL_SIZE`
- `DB_MAX_OVERFLOW`
- `REDIS_MAX_CONNECTIONS`
- `INGESTION_MAX_WORKERS`
- `NEWS_FETCH_INTERVAL_MINUTES`
- `MAX_ITEMS_PER_RUN`
- `LLM_DAILY_COST_CEILING`

## Daily Post-Launch Review

For the first 7 to 14 days after launch, check this once or twice per day:

- Is API response time acceptable for real users?
- Is the worker completing scheduled jobs on time?
- Is `/metrics` showing backlog growth or stalled ingestion?
- Is `/inventory/health` showing thin or imbalanced promoted inventory?
- Are there Redis lock refresh warnings in worker logs?
- Are there DB connection or timeout errors?
- Is the LLM spend ceiling being hit?

## Upgrade Order

If something starts to strain, make changes in this order:

1. Upgrade the worker.
2. Tune worker concurrency and ingestion settings.
3. Upgrade the API.
4. Upgrade DB or Redis if they are the failing dependency.
5. Move to Hetzner only if cost or compute needs justify the ops tradeoff.

## Trigger Rules

### Upgrade Worker First If

- Ingestion backlog keeps growing for more than 2 scheduler cycles.
- Inventory health degrades because fresh/promoted content is not keeping up.
- Worker logs show runs taking too long or repeated failures.
- The worker is CPU or memory constrained in Render.

Suggested first actions:

- Increase the worker plan.
- Revisit `INGESTION_MAX_WORKERS`.
- Revisit `MAX_ITEMS_PER_RUN`.
- Revisit `NEWS_FETCH_INTERVAL_MINUTES`.

### Upgrade API First If

- User-facing feed or playlist requests are slow.
- Worker health looks fine, but request latency rises under user traffic.
- API memory or CPU stays consistently high.

Suggested first actions:

- Increase API plan.
- Add another API instance if traffic becomes sustained and horizontal scaling is warranted.

### Upgrade Redis If

- Worker logs show repeated lock refresh failures.
- Cache operations begin failing regularly.
- `/health` reports Redis instability.

This matters because the worker leader lock is stored in Redis and refreshed continuously.

### Upgrade DB If

- `/health` fails because of DB reachability or query pressure.
- API latency gets worse during ingestion windows.
- Connection pool pressure appears in logs.

## When To Consider Hetzner

Move API and worker to Hetzner only when at least one of these is true for a sustained period:

- You need more compute than Render gives at a price you like.
- You want to materially reduce monthly cost.
- The workload shape is stable enough that a dedicated box makes sense.
- You are comfortable owning:
  - OS updates
  - Docker/service supervision
  - backups
  - monitoring
  - incident response

Do not move only API and worker to Hetzner while leaving DB and Redis on Render unless there is a very specific reason. That adds cross-provider complexity without much early benefit.

## Decision Rule

Use this default rule:

- Stay on Render through launch and early traffic discovery.
- Upgrade the worker before making a platform move.
- Revisit Hetzner after real traffic data shows Render is either too expensive or too constrained.

## Practical Next Step

Before launch, prepare a small internal checklist:

- Save baseline screenshots of Render CPU, memory, and response time.
- Save one healthy `/metrics` response sample.
- Save one healthy `/inventory/health` response sample.
- Decide in advance what counts as "upgrade worker now" and "upgrade API now".

That way, if traffic picks up suddenly, the response is a simple runbook step instead of a rushed hosting debate.
