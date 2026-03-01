# Data Retention & Cleanup Runbook

## Overview

Blips runs an automated data retention system that prunes old records from
PostgreSQL on a daily schedule.  The cleanup job is restart-safe (Redis
distributed lock), idempotent, and protects editorially curated content.

---

## Retention Policies

| Table | Retention Period | Env Var | Default | Notes |
|---|---|---|---|---|
| `content_items` | Content days | `RETAIN_CONTENT_DAYS` | 90 | Editorial/manual items **protected** |
| `ingestion_progress` | Debug days | `RETAIN_INGESTION_PROGRESS_DAYS` | 14 | |
| `ingestion_budgets` | Debug days | `RETAIN_INGESTION_PROGRESS_DAYS` | 14 | Keyed by `day` (date) |
| `source_daily_stats` | Debug days | `RETAIN_INGESTION_PROGRESS_DAYS` | 14 | Keyed by `day` (date) |
| `editorial_actions` | Editorial days | `RETAIN_EDITORIAL_DAYS` | 180 | |
| `interaction_events` | Event days | `RETAIN_EVENTS_DAYS` | 30 | |
| `conversations` | Conversation days | `RETAIN_CONVERSATIONS_DAYS` | 30 | |
| `usage` | Usage days | `RETAIN_USAGE_DAYS` | 90 | |

### Content Protection Rules

Items meeting **any** of these criteria are **never** deleted by retention:

- `editorial_boost > 0` — editorially promoted
- `manual_added = true` — manually added via admin

---

## Schedule

The cleanup job runs at **4:00 AM UTC** daily via APScheduler cron trigger.
Only one replica can run it at a time (Redis NX lock with 10-minute TTL).

---

## Manual Trigger

Trigger cleanup on demand via the admin API:

```bash
curl -X POST https://<host>/admin/maintenance/cleanup \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

Response:

```json
{
  "status": "ok",
  "result": {
    "started_at": "2025-06-01T04:00:01",
    "ended_at": "2025-06-01T04:00:03",
    "duration_seconds": 2.14,
    "total_deleted": 342,
    "tables": {
      "content_items": 120,
      "ingestion_progress": 85,
      "ingestion_budgets": 14,
      "source_daily_stats": 14,
      "editorial_actions": 0,
      "interaction_events": 97,
      "conversations": 12,
      "usage": 0
    },
    "errors": []
  }
}
```

---

## Check Status

```bash
curl https://<host>/admin/maintenance/status \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

Response:

```json
{
  "last_run_at": "2025-06-01T04:00:03",
  "retention_policy": {
    "RETAIN_CONTENT_DAYS": 90,
    "RETAIN_INGESTION_PROGRESS_DAYS": 14,
    "RETAIN_EVENTS_DAYS": 30,
    "RETAIN_CONVERSATIONS_DAYS": 30,
    "RETAIN_USAGE_DAYS": 90,
    "RETAIN_EDITORIAL_DAYS": 180
  }
}
```

---

## Redis TTL Policy

All Redis keys are written with explicit TTLs:

| Key Pattern | TTL | Purpose |
|---|---|---|
| `feed:*`, `curated_feed:*` | 300s (5 min) | Feed cache |
| `blips:item:*` | 3600s (1 hr) | Content item cache |
| `blips:feature:*` | 21600s (6 hr) | Feature flags |
| `blips:lease:*` | 300s (5 min) | Scheduler leader election |
| `blips:cleanup_lock` | 600s (10 min) | Cleanup job lock |
| `blips:cleanup:last_run_at` | 172800s (2 days) | Last cleanup timestamp |

---

## Troubleshooting

### Cleanup never runs

1. Check scheduler is enabled: `SCHEDULER_ENABLED=true`
2. Check Redis connectivity — if Redis is down, the lock is fail-open so cleanup should still run
3. Check logs for `[data_cleanup]` entries
4. Trigger manually via the admin endpoint

### Lock stuck (another instance holds the lock)

The lock auto-expires after 10 minutes.  If you need to force-release:

```bash
redis-cli DEL blips:cleanup_lock
```

### Specific table cleanup fails

Errors are isolated per table.  If one table fails, the others still proceed.
Check `result.errors` in the admin endpoint response or search logs for
`[retention] <table> cleanup failed`.

### Content items not being deleted

Verify the item isn't protected:

```sql
SELECT id, editorial_boost, manual_added, created_at
FROM content_items
WHERE id = '<item-id>';
```

Items with `editorial_boost > 0` or `manual_added = true` are exempt.

---

## Batch Size

Content item deletion is capped at **5,000 rows per run** to avoid long
table locks.  If there is a large backlog, subsequent daily runs will
continue pruning.  For initial bulk cleanup, trigger the admin endpoint
multiple times.

---

## Environment Variables Reference

```env
# Retention periods (days)
RETAIN_CONTENT_DAYS=90
RETAIN_INGESTION_PROGRESS_DAYS=14
RETAIN_EVENTS_DAYS=30
RETAIN_CONVERSATIONS_DAYS=30
RETAIN_USAGE_DAYS=90
RETAIN_EDITORIAL_DAYS=180
RETAIN_DEBUG_DAYS=7

# Redis TTL constants (seconds) — set in config.py, not typically overridden
FEED_CACHE_TTL_SECONDS=300
ITEM_CACHE_TTL_SECONDS=3600
CONFIG_CACHE_TTL_SECONDS=21600
LEASE_TTL_SECONDS=300
```
