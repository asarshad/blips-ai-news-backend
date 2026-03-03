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

---

## Content Extraction Pipeline

### Overview

Each ingested article goes through a **hardened content extraction pipeline** that
fetches the full article page and extracts reliable metadata and text. The pipeline
lives in `app/extraction/` and is orchestrated by `run_extraction()`.

### Architecture

```
RSS Entry
  │
  ▼
┌───────────────┐     ┌──────────────┐     ┌───────────────┐
│  fetcher.py   │────▶│ metadata.py  │────▶│ text_extract  │
│  (httpx)      │     │ (BS4/lxml)   │     │ (trafilatura  │
│               │     │              │     │  → readability │
│  rate-limited │     │ canonical_url│     │  → RSS only)  │
│  retries/backoff    │ og:title/img │     │               │
└───────────────┘     └──────────────┘     └───────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │   pipeline.py    │
                    │ ExtractionResult │
                    │ (never raises)   │
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  normalize.py    │
                    │ URL validation   │
                    │ text quality     │
                    │ boilerplate rm   │
                    └──────────────────┘
```

### Extraction Steps

1. **Fetch** (`fetcher.py`): HTTP GET via httpx with per-domain rate limiting,
   exponential backoff retries (429/5xx/timeouts), 5 MB cap, polite User-Agent.

2. **Metadata** (`metadata.py`): Parse `<head>` with BeautifulSoup/lxml:
   - `canonical_url`: `<link rel="canonical">` or source URL
   - `title`: `og:title` → `twitter:title` → `<title>`
   - `image_url`: `og:image` → `twitter:image` (validated absolute URL or NULL)
   - `published_at`: `article:published_time` and similar meta tags

3. **Text Extraction** (`text_extract.py`): Cascading strategy:
   - **trafilatura** (primary): Best boilerplate removal, `favor_precision=True`
   - **readability-lxml** (fallback): Mozilla Readability port
   - **RSS description** (last resort): Stored as `excerpt_fallback`

4. **Normalization** (`normalize.py`):
   - Image URLs: `data:`, `blob:`, relative, non-http(s) → `NULL`
   - Text: Strip boilerplate lines (newsletter/cookie/copyright patterns)
   - Quality score: 0.0–1.0 based on word count, alnum ratio, boilerplate

### Failure Modes

| Failure | Behavior |
|---------|----------|
| Fetch timeout/error | Falls back to RSS metadata only |
| Metadata parse error | Uses RSS title/image as fallback |
| trafilatura fails | Falls back to readability-lxml |
| readability fails | Falls back to RSS description |
| Invalid image URL | Stored as `NULL` (never invalid) |
| All extractors fail | `extraction_status=FAILED`, RSS excerpt stored |

**Key invariant**: `run_extraction()` **never raises**. All errors are captured
in `ExtractionResult.fetch_error` and `extraction_status`.

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACTION_ENABLED` | `true` | Master switch for extraction pipeline |
| `EXTRACTION_CONNECT_TIMEOUT` | `10` | HTTP connect timeout (seconds) |
| `EXTRACTION_READ_TIMEOUT` | `20` | HTTP read timeout (seconds) |
| `EXTRACTION_MAX_RETRIES` | `3` | Retry attempts on transient errors |
| `EXTRACTION_BACKOFF_BASE` | `1.5` | Exponential backoff base |
| `EXTRACTION_DOMAIN_MIN_INTERVAL` | `1.0` | Per-domain rate limit (seconds) |
| `EXTRACTION_MIN_TEXT_WORDS` | `100` | Min words for "good" text |
| `EXTRACTION_IDEAL_TEXT_WORDS` | `300` | Word count for max quality score |
| `SOURCE_HEALTH_DEGRADED_THRESHOLD` | `0.3` | Health score below which source is degraded |

### Observability

- `GET /metrics/extraction` — Global counters, per-source health, degraded sources
- `GET /metrics/extraction/samples` — Last N extraction samples for debugging

Both endpoints require `ADMIN_API_KEY`.

### Debugging

```bash
# Check extraction health
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://YOUR-SERVICE/metrics/extraction | jq

# Check recent samples
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://YOUR-SERVICE/metrics/extraction/samples?limit=10 | jq

# Look for degraded sources
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://YOUR-SERVICE/metrics/extraction | jq '.degraded_sources'
```

### Tests

```bash
# Run extraction unit tests
python -m pytest tests/unit/extraction/ -v

# Run with coverage
python -m pytest tests/unit/extraction/ --cov=app.extraction --cov-report=term
```
