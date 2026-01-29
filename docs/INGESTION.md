# Durable ingestion (checkpointed)

This backend supports restart-resilient ingestion that continues until per-feed targets are met.

## Goals

- Durable progress: restarts resume from DB checkpoints
- Safe concurrency: multiple API workers won’t duplicate work
- Idempotent inserts: content uses `source_url` uniqueness + `ON CONFLICT DO NOTHING`

## How it works

- Progress is stored in Postgres table `ingestion_progress`.
- Each row is keyed by `(day_utc, source_type, feed_name)` and tracks:
  - `target`, `items_ingested`
  - `last_item_cursor` (newest-first feed cursor)
  - `status` (`running|complete|failed|disabled`)
- Each feed/channel is protected by a Redis lease (`SET NX PX` with a token).
  - Release only deletes if the token matches.
  - If Redis is unavailable, the code falls back to Postgres advisory locks.

## Controls (environment variables)

- `SCHEDULER_ENABLED` (default: `true`): start APScheduler in-process
- `INGESTION_ENABLED` (default: `true`): master switch for ingestion
- `INGESTION_CRON_DISABLED` (default: `false`): emergency stop for scheduled ingestion
- `INGEST_UNTIL_TARGETS` (default: `true`): keep looping until targets are met (within budget)
- `INGEST_CATCHUP_MAX_SECONDS` (default: `600`): max seconds spent per job run
- `INGESTION_POLL_SECONDS` (default: `30`): sleep between loops when targets not met
- `INGESTION_LEASE_TTL_MS` (default: `60000`): per-feed lease TTL
- `INGESTION_TARGET_DEFAULTS`: JSON mapping `{ "rss:TechCrunch": 3, "youtube_video:Bloomberg Technology": 2 }`

## Observability

- `GET /health` returns 200 only if DB is reachable.
- `GET /metrics` returns JSON including per-feed ingestion progress for the current UTC day.

Example:

- `curl -fsS https://YOUR-SERVICE.onrender.com/metrics | jq`

## Inspecting progress (SQL)

```sql
SELECT day_utc, source_type, feed_name, status, items_ingested, target, updated_at
FROM ingestion_progress
WHERE day_utc = CURRENT_DATE
ORDER BY source_type, feed_name;
```

## Resume behavior

On each scheduled run (and the startup-triggered run), the ingestion job:

1. Ensures today’s progress rows exist for configured feeds/channels.
2. Scans for any rows with `status != 'complete'` and `items_ingested < target`.
3. Claims a per-feed lease and ingests until that row hits its target.

If the service restarts mid-run, the next run resumes from the persisted checkpoint.
