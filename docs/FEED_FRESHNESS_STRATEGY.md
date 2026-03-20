# Feed Freshness Strategy

## Overview

The "Rolling Freshness + Reservoir" strategy ensures the Blips app never feels empty at day rollover or during low-ingestion periods. It provides a tiered content delivery system with truthful freshness labeling.

## Problem Solved

**Before**: Articles were queried by `published_at` date, which meant:
- At midnight, the "today" bucket was empty
- During slow news days, feeds felt sparse
- Users saw "No articles" even when content was recently ingested

**After**: A three-tier blending strategy ensures the feed always has content while being honest about freshness.

## Tiered Strategy

### Tier A: Fresh (Published Recently)
- **Criteria**: `published_at >= NOW - fresh_window`
- **Priority**: Highest - shown first
- **Label**: "Published X ago"
- **Badge**: None (implicitly fresh)

### Tier B: Backfill (Recently Added)
- **Criteria**: `created_at >= NOW - backfill_window` AND `published_at < fresh_cutoff`
- **Priority**: Medium - fills gaps after Tier A
- **Label**: "Added X ago · Published Y ago"
- **Badge**: "New to you"

### Tier C: Evergreen (High Quality Archive)
- **Criteria**: `published_at >= NOW - evergreen_window` AND `global_score >= 0.3`
- **Priority**: Lowest - ensures non-empty feeds
- **Label**: "Published X ago"
- **Badge**: "Highlight"

## Surface-Specific Windows

| Surface | Fresh Window | Backfill Window | Evergreen Window | Min Fresh | Reservoir |
|---------|--------------|-----------------|------------------|-----------|-----------|
| Articles | 36 hours | 24 hours | 14 days | 30 | 200 |
| Videos | 72 hours | 48 hours | 30 days | 25 | 150 |
| Reels | 168 hours (7d) | 72 hours | 45 days | 20 | 300 |

## Configuration

All windows are configurable via environment variables:

```bash
# Articles
ARTICLES_FRESH_PUBLISHED_HOURS=36
ARTICLES_BACKFILL_CREATED_HOURS=24
ARTICLES_EVERGREEN_DAYS=14
MIN_FRESH_ARTICLES=30
RESERVOIR_ARTICLES=200

# Videos
VIDEOS_FRESH_PUBLISHED_HOURS=72
VIDEOS_BACKFILL_CREATED_HOURS=48
VIDEOS_EVERGREEN_DAYS=30
MIN_FRESH_VIDEOS=25
RESERVOIR_VIDEOS=150

# Reels
REELS_FRESH_PUBLISHED_HOURS=168
REELS_BACKFILL_CREATED_HOURS=72
REELS_EVERGREEN_DAYS=45
MIN_FRESH_REELS=20
RESERVOIR_REELS=300
```

## Self-Healing Inventory

### Health Check
The inventory service continuously monitors content levels:

```
GET /api/v1/inventory/health
```

Returns:
```json
{
  "is_healthy": true,
  "needs_topup": false,
  "surfaces": {
    "articles": {
      "tier_a": 45,
      "tier_b": 23,
      "tier_c": 132,
      "total": 200,
      "below_min_fresh": false
    }
  },
  "topup_priority": []
}
```

### Auto Top-Up
When inventory falls below thresholds:
1. **Startup**: Checks inventory and triggers ingestion if needed
2. **API Requests**: Background check on `/articles/recent`, `/videos/recent`, `/reels`
3. **Non-blocking**: Top-up runs in background thread, API returns immediately

Top-up controls:
```bash
TOPUP_LOCK_TTL_SECONDS=120    # Prevent concurrent top-ups
TOPUP_MAX_RUNTIME_SECONDS=300 # Max 5 minutes per top-up cycle
HEALTH_CACHE_TTL_SECONDS=60   # Don't check health every request
```

## API Response Fields

Each feed item now includes:

```json
{
  "id": 123,
  "title": "...",
  "freshness_tier": "A",
  "freshness_reason": "fresh_published",
  "published_at": "2026-02-06T10:30:00",
  "created_at": "2026-02-06T11:00:00",
  "published_age_seconds": 3600,
  "added_age_seconds": 1800
}
```

## Caching

Feed results are cached in Redis for 45 seconds:
- **Key pattern**: `blips:tiered_feed:{surface}:l{limit}:o{offset}:ai{0|1}`
- **Invalidation**: After top-up completion
- **Fallback**: Direct DB query if Redis unavailable

## Flutter Integration

### FreshnessLabel Widget
Displays truthful freshness info:
- "Published 2h ago" (Tier A)
- "Added 1h ago · Published 3d ago" (Tier B with "New to you" badge)
- "Published 5d ago" (Tier C with "Highlight" badge)

### FeedEntry Model
```dart
class ArticleFeedEntry extends FeedEntry {
  final DateTime? addedAt;
  final FreshnessTier freshnessTier;
  final String? freshnessReason;
}
```

## Diversity Mixing

After tier selection, content is diversity-mixed to avoid source concentration:
- Maximum 2 consecutive items from same source
- Interleaves sources when possible
- Preserves tier priority within mixing

## Monitoring

Check tier distribution in logs:
```
Articles page 1: {'A': 8, 'B': 5, 'C': 2} (limit=15)
```

Check inventory health:
```bash
curl http://localhost:8000/api/v1/inventory/health | jq
```

## Troubleshooting

### "All items are Tier C"
- Check if RSS feeds are being fetched (`/api/v1/metrics/sources`)
- Verify ingestion is running (`/metrics`)
- Increase `ARTICLES_FRESH_PUBLISHED_HOURS` temporarily

### Top-up not triggering
- Check Redis connectivity
- Verify `MIN_FRESH_ARTICLES` threshold
- Check logs for "Top-up already in progress"

### Cache not invalidating
- Redis connection required
- Check `invalidate_tiered_feed_cache()` called after top-up
