# X (Twitter) Signal Integration

## Core Principle

X is a **signal source only**, not a content source.

We do not ingest tweets as feed items.  
We do not treat X as a publisher.  
We do not surface any tweet, thread, or X post in the feed.

Instead, we observe what URLs reputable tech accounts are sharing, resolve those URLs, and pass them into the existing signal pipeline. The pipeline then decides — using the same canonicalization, dedupe, quality gates, and promotion logic it already uses for HN, GitHub, and YouTube signals — whether those URLs become articles or videos.

---

## Why X as a Signal Source

Hacker News, GitHub Trending, and YouTube Trending each have coverage blind spots:

- **HN** skews toward developer-specific stories; misses product launches, policy news, and mainstream tech events until they've already peaked.
- **GitHub** is code-repository-specific; cannot surface articles, analysis, or video content.
- **YouTube Trending** requires a video to already be popular before it appears.

X is often where a tech story first surfaces — before RSS feeds pick it up, before HN votes accumulate, before YouTube Trending reacts. By watching trusted accounts and topic-relevant searches, we can detect these stories earlier and let the promotion pipeline decide on quality.

---

## How It Works

```
X API (search/recent)
        │
        ▼
   x_signal_fetcher.py
        │
   1. Fetch tweets matching cohort/query criteria
   2. Extract entities.urls[*].expanded_url from each tweet
   3. Validate URLs (safety, content-type, domain policy)
   4. Score URLs (account tier + engagement - time decay)
   5. Return List[SignalItem]  ← same dataclass as HN, GitHub, YT
        │
        ▼
   signal_ingestion.py (existing orchestrator)
        │
   6. normalize_url() — strip tracking params
   7. is_allowed_domain() — domain policy gate
   8. signal_urls upsert — track sightings
   9. _find_existing() — check content_items
        │
    ┌───┴────────────────────────┐
    ▼                            ▼
 URL already exists           URL is new
 → increment signal_hits      → create CANDIDATE stub
 → mark as DUPLICATE          → queue for promotion
    │                            │
    └───────────┬────────────────┘
                ▼
        promotion_service.py (existing, unchanged)
                │
        Compute promotion_score using:
        - source quality (domain_policy)
        - cluster_hotness (signal_hits count)
        - recency
        - velocity (videos)
        - clickbait penalty
                │
        If score ≥ threshold → PROMOTED
        Else → CANDIDATE (re-evaluated next run)
                │
        Content type decided by existing _detect_content_type()
        → ARTICLE (publisher URLs, blog posts, news articles)
        → VIDEO   (YouTube URLs)
```

---

## What Is and Is Not Ingested

### Accepted
- Publisher article URLs (e.g., `techcrunch.com/2026/...`, `arstechnica.com/...`)
- Blog/research post URLs (e.g., `openai.com/blog/...`, `arxiv.org/abs/...`)
- YouTube video watch URLs (e.g., `youtube.com/watch?v=...`)

### Rejected (at fetcher level)
- `twitter.com` / `x.com` self-links
- `t.co` unresolved redirects (Twitter pre-expands these in the API response)
- `pic.twitter.com` / `twimg.com` media links
- Homepage URLs (path is `/` or empty)
- YouTube channel pages (`/channel/`, `/@`, `/c/`, `/user/`)
- Profile pages, tag pages, category pages
- Private/internal IP addresses (SSRF protection)
- Non-http(s) schemes

### Rejected (at orchestrator level, same as all signal sources)
- Domains outside tech policy (`is_allowed_domain()`)
- URLs that fail normalization

---

## Acquisition Modes

### A. Curated Account Cohort (`mode=cohort`)

Queries recent tweets from a maintained allowlist of trusted tech accounts.

Default cohort includes:
- Official company accounts: `@OpenAI`, `@AnthropicAI`, `@GoogleDeepMind`, `@nvidia`
- Key researchers: `@karpathy`, `@ylecun`, `@demishassabis`
- Investors/commentators: `@sama`, `@ycombinator`, `@benedictevans`
- Tech media: `@TechCrunch`, `@verge`, `@wired`, `@arstechnica`

Cohort accounts share a higher `account_tier` weight in the scoring formula.

Extend via `X_SIGNALS_COHORT_ACCOUNTS` (comma-separated usernames, overrides default entirely).

### B. Topical Query (`mode=query`)

Uses Twitter's search operators:
- `has:links` — tweet must contain a URL
- `lang:en` — English only
- `-is:retweet` — exclude retweets (original content only)

Combined with tech-relevant terms:
- `AI model release`
- `LLM benchmark`
- `chip announcement`
- `security breach`
- `open source release`

Extend/override via `X_SIGNALS_QUERY_TERMS` (comma-separated).

### C. Mixed (`mode=mixed`)

Runs both cohort and query fetches. Results are URL-deduplicated before scoring. This is the highest-coverage mode.

**Do not use global trends as the primary mode.** Trends are too noisy and reflect viral culture, not tech news.

---

## Scoring Formula

Each resolved URL receives a `signal_score` (integer, 1–100):

```
signal_score = tier_weight + engagement_bonus - time_decay + 5
```

| Component | Range | Notes |
|-----------|-------|-------|
| `tier_weight` | 40 (cohort) / 15 (query) | Trust tier of source account |
| `engagement_bonus` | 0–20 | `log2(likes + retweets*2) * 2`, capped at 20 |
| `time_decay` | 0–30 | `age_hours * 2.0`, capped at 30 |
| `+5` base | 5 | Minimum floor contribution |

**Design principles:**
- Reputable sharers outweigh raw engagement
- Time decay is aggressive — 15-hour-old tweets score significantly lower
- Raw virality alone cannot auto-promote a URL (promotion_service decides that)
- The `signal_score` stored in `signal_urls` is for observability; promotion is governed by `promotion_service.py`

The `cluster_hotness` component in `promotion_service` is what actually amplifies repeated signal sightings. A URL seen by both X and HN gets `signal_hits=2`, which boosts its promotion score.

---

## Database

### New `signal_urls` source value: `x_signal`

Added to the `signalsource` PostgreSQL enum via migration `x_signal_source_001`.

The `discovered_via` field on content stubs from X will be `"signal_x"`.

### No new tables or columns required.

X signals flow through the existing pipeline entirely.

---

## Configuration

All X signal settings are in `core/config.py` and read from environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_X_SIGNALS_ENABLED` | `false` | Master kill switch. Set via env or Redis. |
| `X_BEARER_TOKEN` | `""` | Twitter API v2 Bearer Token (required for any mode). |
| `X_SIGNALS_MODE` | `"cohort"` | `off` \| `cohort` \| `query` \| `mixed` |
| `X_SIGNALS_MAX_ITEMS_PER_RUN` | `50` | Cap on SignalItems returned per run |
| `X_SIGNALS_MIN_SCORE` | `10` | Minimum signal score to include an item |
| `X_SIGNALS_ALLOWED_DOMAINS` | `""` | Optional comma-separated domain allowlist (empty = all tech domains) |
| `X_SIGNALS_RATE_LIMIT_ENABLED` | `true` | Sleep 1s between API calls |
| `X_SIGNALS_DEBUG_LOGGING` | `false` | Verbose per-URL rejection logging |
| `X_SIGNALS_COHORT_ACCOUNTS` | `""` | Override default cohort (comma-separated usernames, no @) |
| `X_SIGNALS_QUERY_TERMS` | `""` | Override default query terms (comma-separated) |
| `X_SIGNALS_MAX_TWEET_AGE_HOURS` | `24` | Filter tweets older than N hours |
| `X_SIGNALS_REQUEST_TIMEOUT` | `15` | HTTP timeout per API call (seconds) |

---

## How to Disable Instantly

**Option 1: Environment variable (no redeploy needed if using Redis feature flags)**
```bash
# Via Redis (instant, no restart)
redis-cli SET blips:feature:x_signals false

# Via environment variable (requires restart)
FEATURE_X_SIGNALS_ENABLED=false
```

**Option 2: Mode flag (also disables without touching feature flags)**
```bash
X_SIGNALS_MODE=off
```

**Behavior when disabled:**
- `run_signal_ingestion_job()` skips X entirely
- No X code path runs
- No side effects on other signal sources
- All other sources (HN, GitHub, YT, Discovery) continue normally
- Feed behavior is unchanged

---

## Operational Risks

### 1. API Rate Limits
Twitter API v2 free tier allows 10 requests per 15 minutes for recent search.  
Basic tier allows 60 requests per 15 minutes.

At default settings (mixed mode, ~20 queries), the basic tier is required.  
Mitigation: `X_SIGNALS_RATE_LIMIT_ENABLED=true` adds 1s sleep between calls.  
Further reduce with `X_SIGNALS_MODE=cohort` (fewer queries).

### 2. API Cost
Twitter API basic tier costs ~$100/month.  
Mitigation: Feature flag is `false` by default. Enable only when intentional.

### 3. Signal Noise
Low-quality accounts can share clickbait. Mitigations:
- Cohort mode restricts to trusted accounts
- `X_SIGNALS_MIN_SCORE` filters low-confidence signals
- `is_allowed_domain()` gate rejects poor domains
- `compute_clickbait_penalty()` in promotion_service catches clickbait titles

### 4. Legal/TOS
Twitter API TOS restricts some uses. This implementation only reads public tweets via the official API and does not store tweet content (only the extracted URLs).  
Mitigation: Easy kill switch (`FEATURE_X_SIGNALS_ENABLED=false`).

### 5. API Unavailability
If X API fails: log the error, return empty list, do not raise exception, do not block other signal sources.

---

## Metrics and Debugging

Metrics logged per run (structured log at INFO level):

| Metric | Description |
|--------|-------------|
| `x_signal_posts_seen` | Total tweets fetched from API |
| `x_signal_urls_extracted` | Raw URLs from tweet entities |
| `x_signal_urls_resolved` | URLs that passed all validation checks |
| `x_signal_urls_rejected` | URLs rejected (safety/domain/score) |
| `x_signal_items_returned` | SignalItems passed to orchestrator |
| `x_signal_errors` | API or processing errors |

Enable per-URL rejection reasoning with `X_SIGNALS_DEBUG_LOGGING=true`.

---

## Testing

Run the X signal unit tests:
```bash
pytest tests/unit/ingestion/test_x_signal_fetcher.py -v
```

Key test cases:
- URL extraction from mock tweet payloads
- Twitter/social URL rejection
- Homepage/channel URL rejection
- Private IP SSRF rejection
- Score calculation bounds
- Feature flag disabled behavior
- Missing bearer token graceful failure
- YouTube watch URL acceptance
- YouTube channel URL rejection
