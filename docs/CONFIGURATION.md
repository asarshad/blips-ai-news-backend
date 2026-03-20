# Configuration Reference

This document describes the current backend configuration surface.

Primary sources of truth:

- `src/backend/app/core/config.py`
- `src/backend/app/core/feature_flags.py`
- env-only scheduler/runtime knobs in:
  - `src/backend/app/main.py`
  - `src/backend/app/scheduler/__init__.py`
  - `src/backend/app/scheduler/config.py`

If this document, `.env.example`, and the code disagree, the code wins and the docs should be updated immediately.

## Loading model

- Most backend settings are loaded through `pydantic-settings` in `app/core/config.py`.
- A smaller set of runtime knobs is read directly from `os.getenv(...)` in scheduler/worker code.
- Feature flags resolve in this order:
  1. Redis key `blips:feature:{name}`
  2. env var fallback `FEATURE_{NAME}_ENABLED`
  3. defaults in `app/core/feature_flags.py`

## Core runtime

| Variable | Default | Notes |
| --- | --- | --- |
| `ENV` | `dev` | `dev` or `prod` |
| `LOG_LEVEL` | `INFO` | Standard Python log levels |
| `ADMIN_API_KEY` | `""` | Required for admin, metrics, and ops endpoints in production |
| `CORS_ORIGINS` | `""` | Empty falls back to `capacitor://localhost,http://localhost` |
| `DOCS_ENABLED` | `false` | Enables `/docs` and `/redoc` |
| `DEBUG_ROUTES_ENABLED` | `false` | Mounts conditional debug routes |
| `RATE_LIMIT_DEFAULT` | `60/minute` | Default per-IP rate limit |
| `RATE_LIMIT_CHAT` | `10/minute` | Chat-specific rate limit |
| `ALERT_ENABLED` | `false` | Enables webhook-based alerting |
| `ALERT_WEBHOOK_URL` | `""` | Alert destination |

## Database and Redis

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/blips` | Primary Postgres connection |
| `DB_POOL_SIZE` | `3` | SQLAlchemy pool size per process |
| `DB_MAX_OVERFLOW` | `5` | Extra DB connections above pool size |
| `DB_POOL_TIMEOUT` | `30` | Seconds to wait for a DB connection |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | Connection recycle interval |
| `REDIS_URL` | `redis://redis:6379/0` | Primary Redis connection |
| `REDIS_MAX_CONNECTIONS` | `20` | Shared Redis pool ceiling |

## LLM providers and external APIs

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai` | Supported: `openai`, `mistral` |
| `OPENAI_API_KEY` | `""` | Required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o-mini` | Current OpenAI default |
| `MISTRAL_API_KEY` | `""` | Required when `LLM_PROVIDER=mistral` |
| `MISTRAL_MODEL` | `mistral-small-latest` | Current Mistral default |
| `LLM_REQUEST_TIMEOUT` | `30` | Seconds per LLM request |
| `LLM_DAILY_COST_CEILING` | `5.0` | Estimated daily spend ceiling in USD; `0` disables the ceiling |
| `YOUTUBE_API_KEY` | unset | Used directly by video discovery, trending, and reliable Shorts detection |

## Scheduler and ingestion

These settings are spread across `app/core/config.py`, `app/main.py`, and scheduler modules.

| Variable | Default | Notes |
| --- | --- | --- |
| `SCHEDULER_ENABLED` | `true` | Env-only. In production the API runs with `false`; the worker runs with `true` |
| `SCHEDULER_LOCK_TTL_SECONDS` | `120` | Env-only Redis leader-lock TTL |
| `SCHEDULER_LOCK_REFRESH_SECONDS` | `30` | Env-only leader-lock refresh cadence |
| `INGESTION_ENABLED` | `true` | Master ingestion switch |
| `INGESTION_CRON_DISABLED` | `false` | Emergency stop for scheduled ingestion |
| `INGESTION_MAX_WORKERS` | `1` | Parallel ingestion worker count |
| `INGESTION_SCHEDULER_MINUTES` | `15` effective default | Preferred cadence knob; scheduler clamps values into the supported 5-15 minute range |
| `NEWS_FETCH_INTERVAL_MINUTES` | `30` | Backward-compatible fallback if `INGESTION_SCHEDULER_MINUTES` is unset; values are also clamped to 5-15 minutes |
| `NEWS_FETCH_INTERVAL_HOURS` | `3` | Legacy fallback only; not recommended for continuous ingestion |
| `MAX_ITEMS_PER_RUN` | `100` | Env-only cap for per-run processing |
| `MAX_LLM_CALLS_PER_RUN` | `50` | Env-only cap for AI retry work |
| `LLM_RATE_LIMIT_DELAY` | `0.5` | Env-only pause between LLM calls |
| `INGEST_UNTIL_TARGETS` | `true` | Catch-up loop master switch |
| `INGEST_CATCHUP_MAX_SECONDS` | `1800` | Max catch-up runtime |
| `RSS_ENTRIES_PER_FEED` | `50` | Per-feed RSS fetch depth |
| `YT_VIDEOS_PER_CHANNEL` | `30` | Per-channel YouTube fetch depth |
| `YT_CURATED_LOOKBACK_HOURS` | `168` | Video curation lookback window |
| `YOUTUBE_CURATED_ONLY` | `false` | Disable discovery and use curated channels only |
| `YOUTUBE_DISCOVERY_ENABLED` | `true` | Enable search/discovery-based channel expansion |
| `DISCOVERY_SIGNAL_ENABLED` | `true` | Enable Substack/Beehiiv signal ingestion |
| `DISCOVERY_SIGNAL_LIMIT` | `25` | Total signals per run |
| `DISCOVERY_SIGNAL_PER_SOURCE_LIMIT` | `5` | Per-source signal cap |
| `INGESTION_TARGET_DEFAULTS` | `""` | JSON overrides keyed by `{source_type}:{feed_name}` |

### Advanced YouTube discovery controls

These knobs are read directly by discovery/quota/bootstrap code rather than by `Settings`.

| Variable | Default | Notes |
| --- | --- | --- |
| `YOUTUBE_SEARCH_MIN_INTERVAL_MINUTES` | `180` | Shared discovery cooldown fallback |
| `YOUTUBE_VIDEO_SEARCH_MIN_INTERVAL_MINUTES` | inherits shared fallback | Video discovery cooldown override |
| `YOUTUBE_REEL_SEARCH_MIN_INTERVAL_MINUTES` | inherits shared fallback | Reels discovery cooldown override |
| `YOUTUBE_SEARCH_MIN_VIDEO_DEFICIT` | `1` | Minimum video deficit before search expands |
| `YOUTUBE_SEARCH_MIN_REEL_DEFICIT` | `1` | Minimum reels deficit before search expands |
| `YOUTUBE_VIDEO_TRENDING_ENABLED` | `0` | Enable trending-assisted long-form discovery |
| `YOUTUBE_REEL_TRENDING_ENABLED` | `0` | Enable trending-assisted reels discovery |
| `YOUTUBE_API_SEARCH_DAILY_BUDGET_UNITS` | `4500` | Search-unit quota budget |
| `YOUTUBE_API_DURATION_DAILY_BUDGET_UNITS` | `800` | Duration-check quota budget |
| `YT_CHANNEL_BOOTSTRAP_ENABLED` | `true` | Latest-first bootstrap for newly added channels |
| `YT_BOOTSTRAP_MAX_PROFILE_AGE_DAYS` | `2` | Bootstrap age gate |
| `YT_BOOTSTRAP_MAX_ROWS` | `32` | Max bootstrap rows per run |
| `YT_BOOTSTRAP_BATCH_SIZE` | `10` | Bootstrap processing batch size |

## Feed, session, and personalization

| Variable | Default | Notes |
| --- | --- | --- |
| `DEFAULT_PLAYLIST_SIZE` | `50` | Mixed-feed size target |
| `ARTICLE_CACHE_COUNT` | `5` | Article pre-cache count |
| `PLAYLIST_CACHE_TTL_SECONDS` | `300` | Session playlist cache TTL |
| `SESSION_SNAPSHOT_TTL_SECONDS` | `3600` | Session snapshot TTL |
| `FEED_CACHE_TTL_SECONDS` | `300` | Feed cache TTL |
| `ITEM_CACHE_TTL_SECONDS` | `3600` | Per-item cache TTL |
| `CONFIG_CACHE_TTL_SECONDS` | `21600` | Config / feature flag TTL |
| `LEASE_TTL_SECONDS` | `300` | Distributed lease TTL |
| `MAX_CONTENT_AGE_HOURS` | `72` | Content age ceiling for serving |
| `RECENCY_HALF_LIFE_HOURS` | `24` | Recency decay |
| `PREFERENCE_DECAY_FACTOR` | `0.95` | Slower decay as value approaches `1.0` |
| `MAX_TOPIC_DOMINANCE` | `0.40` | Prevent single-topic feed domination |
| `MAX_MESSAGES_PER_DAY` | `5` | Per-device chat quota |
| `MAX_MESSAGES_PER_ARTICLE` | `3` | Per-article chat quota |
| `CLUSTER_WINDOW_HOURS` | `48` | Story-clustering time window |
| `MIN_CLUSTER_SIMILARITY` | `0.5` | Story-clustering threshold |
| `PERSONALIZATION_TOPIC_WEIGHT` | `0.35` | Personalization mix weight |
| `PERSONALIZATION_ENTITY_WEIGHT` | `0.30` | Personalization mix weight |
| `PERSONALIZATION_SOURCE_WEIGHT` | `0.20` | Personalization mix weight |
| `PERSONALIZATION_FORMAT_WEIGHT` | `0.15` | Personalization mix weight |
| `TREND_CLUSTER_WEIGHT` | `0.80` | Trend scoring weight |
| `TREND_ENGAGEMENT_WEIGHT` | `0.20` | Trend scoring weight |

## Inventory, freshness, and extraction

| Variable | Default | Notes |
| --- | --- | --- |
| `DAILY_TARGET_ARTICLES` | `100` | Daily article ingestion target |
| `DAILY_TARGET_VIDEOS` | `60` | Daily video ingestion target |
| `DAILY_TARGET_REELS` | `40` | Daily reels ingestion target |
| `ARTICLES_FRESH_PUBLISHED_HOURS` | `36` | Tier A article freshness window |
| `ARTICLES_BACKFILL_CREATED_HOURS` | `24` | Tier B article window |
| `ARTICLES_EVERGREEN_MAX_DAYS` | `14` | Tier C article max age |
| `VIDEOS_FRESH_PUBLISHED_HOURS` | `168` | Tier A video window |
| `VIDEOS_BACKFILL_CREATED_HOURS` | `72` | Tier B video window |
| `VIDEOS_EVERGREEN_MAX_DAYS` | `30` | Tier C video max age |
| `VIDEOS_REFRESH_PUBLISHED_HOURS` | `36` | Video refresh window |
| `MIN_REFRESH_VIDEOS` | `8` | Video refresh threshold |
| `REELS_FRESH_PUBLISHED_HOURS` | `168` | Tier A reels window |
| `REELS_BACKFILL_CREATED_HOURS` | `72` | Tier B reels window |
| `REELS_EVERGREEN_MAX_DAYS` | `45` | Tier C reels max age |
| `REELS_REFRESH_PUBLISHED_HOURS` | `24` | Reels refresh window |
| `MIN_REFRESH_REELS` | `12` | Reels refresh threshold |
| `REEL_MAX_DURATION_SECONDS` | `180` | Max duration for content to stay in reels |
| `MIN_FRESH_ARTICLES` | `50` | Minimum fresh article inventory |
| `MIN_FRESH_VIDEOS` | `60` | Minimum fresh video inventory |
| `MIN_FRESH_REELS` | `40` | Minimum fresh reels inventory |
| `RESERVOIR_ARTICLES` | `250` | Article reservoir size |
| `RESERVOIR_VIDEOS` | `180` | Video reservoir size |
| `RESERVOIR_REELS` | `320` | Reels reservoir size |
| `TOPUP_LOCK_TTL_SECONDS` | `120` | Top-up lock TTL |
| `TOPUP_MAX_RUNTIME_SECONDS` | `300` | Top-up max runtime |
| `INVENTORY_HEALTH_CACHE_TTL` | `60` | Inventory-health cache TTL |
| `EXTRACTION_ENABLED` | `true` | Enable extraction hardening path |
| `EXTRACTION_CONNECT_TIMEOUT` | `10.0` | Extraction connect timeout |
| `EXTRACTION_READ_TIMEOUT` | `20.0` | Extraction read timeout |
| `EXTRACTION_MAX_RETRIES` | `3` | Extraction retries |
| `EXTRACTION_BACKOFF_BASE` | `1.5` | Extraction retry backoff |
| `EXTRACTION_DOMAIN_MIN_INTERVAL` | `1.0` | Per-domain request floor |
| `EXTRACTION_MIN_TEXT_WORDS` | `100` | Minimum extracted text threshold |
| `EXTRACTION_IDEAL_TEXT_WORDS` | `300` | Target extracted text size |
| `SOURCE_HEALTH_DEGRADED_THRESHOLD` | `0.3` | Source-health warning threshold |

## Ads and retention

| Variable | Default | Notes |
| --- | --- | --- |
| `ADS_ENABLED` | `false` | Legacy backend-injected ads master switch |
| `ADS_FEED_CARD_ENABLED` | `false` | Legacy backend in-feed ads |
| `ADS_BANNER_ENABLED` | `false` | Legacy backend banner ads |
| `ADS_FEED_FREQUENCY` | `0` | Legacy backend ad cadence |
| `ADS_CANARY_PERCENT` | `0` | Legacy backend rollout |
| `ADS_RUNTIME_ENABLED` | `true` | Runtime mobile-ads config master switch |
| `ADS_PROVIDER` | `admob_native` | Runtime ads provider |
| `ADS_RUNTIME_CANARY_PERCENT` | `5` | Runtime ads rollout |
| `ADS_CONFIG_TTL_SECONDS` | `300` | Runtime ads config TTL |
| `ADS_ARTICLES_ENABLED` | `true` | Article ad placements |
| `ADS_ARTICLES_FREQUENCY` | `8` | Article ad cadence |
| `ADS_ARTICLES_FIRST_SLOT_AFTER` | `2` | First article ad slot |
| `ADS_VIDEOS_ENABLED` | `true` | Video ad placements |
| `ADS_VIDEOS_FREQUENCY` | `8` | Video ad cadence |
| `ADS_VIDEOS_FIRST_SLOT_AFTER` | `2` | First video ad slot |
| `ADS_REELS_ENABLED` | `false` | Reels ad placements |
| `ADS_REELS_FREQUENCY` | `0` | Reels ad cadence |
| `ADS_REELS_FIRST_SLOT_AFTER` | `0` | First reels ad slot |
| `RETAIN_CONTENT_DAYS` | `90` | Content retention |
| `RETAIN_INGESTION_PROGRESS_DAYS` | `14` | Ingestion progress retention |
| `RETAIN_EVENTS_DAYS` | `30` | Event retention |
| `RETAIN_CONVERSATIONS_DAYS` | `30` | Conversation retention |
| `RETAIN_USAGE_DAYS` | `90` | Usage retention |
| `RETAIN_EDITORIAL_DAYS` | `180` | Editorial audit retention |
| `RETAIN_DEBUG_DAYS` | `7` | Debug retention |
| `AUTO_APPROVE_REVIEW_CONTENT` | `false` | Promote review-candidate content immediately when intentionally enabled |

## Feature flags

Known logical feature names and their env fallback names:

| Feature name | Env fallback | Default |
| --- | --- | --- |
| `ingestion` | `FEATURE_INGESTION_ENABLED` | `true` |
| `summarization` | `FEATURE_SUMMARIZATION_ENABLED` | `true` |
| `chat` | `FEATURE_CHAT_ENABLED` | `false` |
| `reels` | `FEATURE_REELS_ENABLED` | `true` |
| `videos` | `FEATURE_VIDEOS_ENABLED` | `true` |
| `clustering` | `FEATURE_CLUSTERING_ENABLED` | `true` |
| `personalization` | `FEATURE_PERSONALIZATION_ENABLED` | `true` |
| `signals` | `FEATURE_SIGNALS_ENABLED` | `true` |
| `promotion` | `FEATURE_PROMOTION_ENABLED` | `true` |
| `video_hybrid_rerank` | `FEATURE_VIDEO_HYBRID_RERANK_ENABLED` | `true` |

Runtime management:

- Redis key format: `blips:feature:{name}`
- List flags: `GET /api/v1/admin/flags`
- Inspect one flag: `GET /api/v1/admin/flags/{feature}`
- Override a flag: `PUT /api/v1/admin/flags/{feature}`
- Remove Redis override: `DELETE /api/v1/admin/flags/{feature}`

## Local development baseline

1. Copy `src/backend/.env.example` to `src/backend/.env`.
2. Set `ADMIN_API_KEY`.
3. Set either the OpenAI or Mistral API key for the provider you want to use.
4. Set `YOUTUBE_API_KEY` if you need reliable Shorts detection, trending discovery, or YouTube search-based discovery.
5. For local docs and debug work, enable `DOCS_ENABLED=true` and `DEBUG_ROUTES_ENABLED=true`.

## Documentation discipline

When adding or renaming backend configuration:

1. Update `src/backend/app/core/config.py` or the relevant env-only module.
2. Update `src/backend/.env.example`.
3. Update this document.
4. Update `docs/OPERATIONS.md` if the setting affects deploy or incident handling.
