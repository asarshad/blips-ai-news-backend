# Inventory Health Runbook

Quick operational guide for monitoring and maintaining feed content inventory.

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
