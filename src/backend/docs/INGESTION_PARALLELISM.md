# Ingestion Parallelism

This backend ingests three content categories concurrently:
- ARTICLE (RSS)
- VIDEO (YouTube long-form)
- REEL (YouTube shorts)

It runs in-process (no separate worker service) and is designed to be restart-resilient.

## Key Concepts

- **Durable progress**: `ingestion_progress` rows store per-source checkpoints and counters.
- **Idempotent inserts**: content inserts use `ON CONFLICT DO NOTHING` on `source_url`.
- **Leases**: a Redis lease per (day, source_type, feed_name) prevents double-ingest.
- **Fair scheduling**: the scheduler interleaves tasks across ARTICLE/VIDEO/REEL and enforces per-type concurrency caps.

## Timezone (“today”)

The ingestion day is computed in a configurable timezone:
- `INGESTION_TIMEZONE` (default: `America/Toronto`)

This value is used by ingestion and `/metrics` to decide which day’s `ingestion_progress` rows to operate on.

## Concurrency Tuning

- `INGESTION_MAX_WORKERS` (default: `1`)
- `INGESTION_MAX_WORKERS_ARTICLE` / `INGESTION_MAX_WORKERS_VIDEO` / `INGESTION_MAX_WORKERS_REEL`
  - If unset (or `0`), defaults are derived from `INGESTION_MAX_WORKERS`.
- `INGESTION_BATCH_SIZE` (default: `10`)
- `INGESTION_LOOP_SLEEP_SECONDS` (default: `10`)

Recommended starting point:
- `INGESTION_MAX_WORKERS=3` (one slot per content category)
- Increase to `6` once stable.

## Retry / Backoff

If a task fails, it is scheduled for retry using:
- `INGESTION_RETRY_BASE_SECONDS` (default: `5`)
- `INGESTION_RETRY_MAX_SECONDS` (default: `300`)

Retry state is visible in `/metrics` fields `retry_at` and `retry_count`.

## Validation Checklist

1) Confirm “today” matches Toronto
- Hit `/metrics` and verify `ingestion_day` matches Toronto calendar date.

2) Confirm concurrency/fairness
- Set `INGESTION_MAX_WORKERS=3` and start ingestion.
- Hit `/metrics` and confirm `scheduler.scheduler.per_type_active` shows activity in ARTICLE/VIDEO/REEL when work exists.

3) Confirm attempted vs ingested
- In `/metrics`, check `items_attempted` increases even if `items_ingested` is flat (duplicates).

4) Confirm retry behavior
- Force an integration error for a feed/channel.
- Verify `/metrics` shows a non-null `retry_at` and incrementing `retry_count`.

## Troubleshooting

- **Low inserted, high attempted**: dedupe is filtering duplicates; consider increasing `RSS_ENTRIES_PER_FEED` / `YT_VIDEOS_PER_CHANNEL` or widening source set.
- **Tasks stuck locked**: check Redis connectivity and lease TTL (`INGESTION_LEASE_TTL_MS`).
- **No work dispatched**: ensure `INGESTION_ENABLED=true` and the scheduler leader is running.
