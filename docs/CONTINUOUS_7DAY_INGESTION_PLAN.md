# Plan: Continuous 7-Day Video/Reel Ingestion

## Implementation Progress

| Step | Status | Notes |
|------|--------|-------|
| Step 1: Enable Discovery + Quota Config | ✅ Done | youtube_quota.py defaults (6000/4500/800), per-surface cooldown, config flipped |
| Step 2: Remove Daily Stop Conditions | ✅ Done | service.py un-gated, checkpoint reopen logic, budget 10x multiplier |
| Step 3: Unify Reel Classification ≤120s | ✅ Done | config.py + youtube_client.py updated, reclassify_reels.py verified |
| Step 4: Replace Auto-Pause with Demotion | ✅ Done | Removed auto-pause guardrail, metrics report demoted_channels |
| Step 5: Auto-Graduation for Discovery | ✅ Done | Graduated transitions, 7d cooldown, 14d probation, 16 tests |
| Step 6: Expand Reel Source Roster | ✅ Done | 3 new reel query packs; channel additions need manual ID verification |
| Step 7: Feed Diversity Caps | ⬜ Not started | |
| Step 8: Feed Endpoints & caught_up | ⬜ Not started | |
| Step 9: Strengthen Language Filtering | ⬜ Not started | |
| Step 10: Extend Admin Metrics | ⬜ Not started | |

All 701 unit tests passing.

---

## TL;DR
Shift videos and reels from daily target budgeting to a continuous rolling 7-day inventory model. Broaden intake via curated + always-on discovery. Keep quality strict at promotion/ranking, not at intake. Unify reel classification. Since the app is pre-release, ship as two phases without backward-compat constraints.

---

## Phase A — Feature Changes (ship first)

### Step 1: Enable Discovery in Production Config
- Set `YOUTUBE_CURATED_ONLY=false`, `YOUTUBE_DISCOVERY_ENABLED=true` in worker config
- Add surface-specific discovery cooldown config: `YOUTUBE_VIDEO_SEARCH_MIN_INTERVAL_MINUTES=180` initially and `YOUTUBE_REEL_SEARCH_MIN_INTERVAL_MINUTES=120` initially, with fallback to the existing shared `YOUTUBE_SEARCH_MIN_INTERVAL_MINUTES` until the code is migrated
- **Current code caveat**: `youtube_quota.py` currently supports only one shared cooldown env (`YOUTUBE_SEARCH_MIN_INTERVAL_MINUTES`) even though cooldown keys are tracked per surface. Phase A must split this if videos and reels are meant to run at different cadences.
- **Cooldown split scope**: The split is config-level only — add new env vars and update `youtube_quota.py` to read the surface-specific env when checking/recording cooldowns. The existing per-surface cooldown key tracking in Redis stays unchanged; only the interval value source changes from one shared env to a per-surface lookup.
- **YouTube API quota constraint**: Default YouTube Data API quota is 10,000 units/day. Each `search.list` costs 100 units.
  - **Recommended**: Start at 3-hour video search interval and 2-hour reel search interval. At this cadence the estimated daily usage is ~4,700 units (search ~4,000 + duration ~640 + trending ~20), well under the 10k YouTube default.
  - **Internal budget targets (conservative cadence)**: `YOUTUBE_API_DAILY_BUDGET_UNITS=6000`, `YOUTUBE_API_SEARCH_DAILY_BUDGET_UNITS=4500`, `YOUTUBE_API_DURATION_DAILY_BUDGET_UNITS=800`. These give ~27% headroom over estimated usage while staying under the YouTube 10k default.
  - **Post quota-increase targets** (after YouTube approves higher quota): total=15,000 / search=12,000 / duration=2,000. These support 1h video / 30-45m reel intervals.
  - **Important**: internal quota budgets already represent real YouTube quota units. The current defaults (`3000` total, `1800` search, `600` duration) are conservative real-unit budgets, not "API call counts" that should be multiplied by 100.
- **Files**: `src/backend/app/core/config.py`, `src/backend/app/core/youtube_quota.py`, `src/backend/app/services/video_discovery_service.py`, deployment env vars

### Step 2: Remove Daily Stop Conditions for Videos & Reels Across Both Live and Checkpointed Paths
- In `src/backend/app/ingestion/service.py` (~L760-900): remove the daily `remaining_videos` / `remaining_reels` gating for curated YouTube ingest and the `created_remaining_*` gating for discovery ingest. Videos/reels should always be eligible for ingestion every scheduler cycle.
- Because the scheduled worker and top-up both call `run_checkpointed_ingestion`, Phase A must also make checkpointed YouTube rows continuously pollable:
  - stop using `_should_fill_surface()` freshness thresholds to suppress `youtube_video` / `youtube_reel` row creation in `checkpoint_defaults.py`
  - stop treating YouTube progress rows as "done for today" once `items_ingested >= target`; for YouTube rows only, preserve `last_item_cursor` but reopen/reset the row each scheduler or top-up invocation, or implement equivalent per-cycle batch semantics so the same channel can be polled again before the next UTC day
  - when a YouTube row is reopened/reset, clear the state that would otherwise permanently exhaust it: reset `items_attempted`, `retry_count`, `retry_at`, `last_error`, and any `failed` / `complete` status back to an eligible running state
  - keep RSS/article daily checkpoint behavior unchanged in Phase A
- Keep per-run safety caps: `INGEST_CATCHUP_MAX_SECONDS=1800`, per-channel `daily_video_cap`/`daily_reel_cap` from channel config, max batch size per source fetch.
- **Do NOT defer all checkpoint work to Phase B.** Without the YouTube checkpoint changes above, the worker and top-up paths remain day-gated even if the live ingestion service is made continuous.
- **Do NOT remove `IngestionBudget` table/locks yet.** Keep budget locking for Phase A, but convert video/reel checkpoint targets from "daily done-ness" to per-cycle batch semantics for the worker paths.
- **Top-up concurrency**: Both the scheduler and `topup_service.py` call `run_checkpointed_ingestion`. When both paths reopen/reset YouTube progress rows, they must coordinate to avoid resetting a cursor that the other process is mid-way through. Use the same feed-scoped lease / advisory lock already used for worker execution (`ingestion:{day}:{source_type}:{feed_name}`), or acquire a row-level `FOR UPDATE` lock on the specific `ingestion_progress` row before any reset. Do **not** rely on the `IngestionBudget` lock for cursor safety; it is scoped per `(day, content_type)`, not per feed. Add a test that verifies two concurrent checkpoint runs on the same feed don't clobber each other's cursor or reset state.
- **Files**: `src/backend/app/ingestion/service.py`, `src/backend/app/ingestion/checkpointing.py`, `src/backend/app/ingestion/checkpoint_defaults.py`, `src/backend/app/ingestion/checkpoint_worker.py`, `src/backend/app/repositories/ingestion_progress_repo.py`, `src/backend/app/services/topup_service.py`

### Step 3: Unify Reel Classification to ≤120 seconds
- Change `REEL_MAX_DURATION_SECONDS` from 180 → 120 in config.py
- Fix the `YT_SHORT_MAX_SECONDS` env default from 75 → 120 in youtube_client.py (~L838) to match
- Update tiered_feed_service.py duration filter to use the config value (already does, just verify)
- **Phase A classification rule**:
  - `duration > 120s` → always VIDEO
  - `duration <= 120s` → REEL, with `/shorts/` URL, shorts-native channel, and `#shorts` title remaining supporting positive signals
  - `duration missing` + explicit Shorts signal → REEL
  - `duration missing` + no explicit Shorts signal → VIDEO
- **Do not use `player.embedHeight` / `player.embedWidth` in Phase A.** Treat those fields as unvalidated embed metadata, not reliable source-video orientation. If a later phase wants to downgrade short landscape clips into VIDEO, validate a trustworthy orientation signal on a labeled sample first.
- Run `tools/reclassify_reels.py` to reclassify existing 121-180s items from REEL → VIDEO. Verify the script uses a strict `duration > 120` condition (not `>=`) so items at exactly 120s remain classified as REEL.
- **Files**: `src/backend/app/core/config.py`, `src/backend/app/integrations/youtube_client.py` (~L837-841, ~L1194-1265), `src/backend/app/ingestion/service.py` (~L337-356), `src/backend/app/services/tiered_feed_service.py` (~L175-195), `tools/reclassify_reels.py`

### Step 4: Replace Reel Auto-Pause with Soft Demotion
- Remove auto-pause guardrail in checkpoint_defaults.py (~L25-27, ~L229-236) that pauses channels when `(attempts >= 60 AND inserted == 0)` or `(conversion < 2% for 5 consecutive days)`
- Replace with source health demotion: channels with consistently poor yield get their `VideoSourceProfile.status` demoted (e.g., `rotation` → `discovery`, or flagged `low_yield`), reducing their promotion multiplier but not removing them from ingestion
- Update admin metrics endpoint to report demoted (not paused) channels
- **Files**: `src/backend/app/ingestion/checkpoint_defaults.py`, `src/backend/app/services/video_source_service.py`, `src/backend/app/api/routes/metrics.py`

### Step 5: Implement Auto-Graduation for Discovery Channels
- Add periodic evaluation (run alongside promotion every 30 min or as standalone hourly job):
  - `discovery → rotation`: channel has ≥3 promoted items in last 7 days AND promotion_rate_7d ≥ 25% AND clickbait_rate_7d < 10%
  - `rotation → core`: channel has ≥10 promoted items in last 30 days AND promotion_rate_7d ≥ 40% AND freshness_yield_7d ≥ 30%
  - `rotation/core → discovery` (demotion): promotion_rate_7d < 10% for 14 consecutive days
  - **Anti-oscillation**: require 7-day cooldown after any status change before re-evaluation
  - **Freshly graduated probation**: channels that graduated from `discovery → rotation` within the last 14 days use a shorter demotion window (7 days at <10% instead of 14). This prevents a newly promoted low-quality channel from producing poor content for two weeks before correction.
- Persist transition state on `VideoSourceProfile` so these rules are enforceable across runs:
  - add `status_changed_at` to record the last status transition timestamp
  - add `probation_until` to represent temporary post-graduation probation
  - update graduation logic to compare against these persisted fields rather than inferring from `updated_at`
- Remove discovery-lane penalty for channels at `rotation` or `core` status. Penalty (0.03 videos, 0.04 reels) should only apply to channels whose `VideoSourceProfile.status == 'discovery'` — not based on the ingestion lane they were fetched from. A discovery-lane fetch for a `rotation`-status channel must not be penalized.
- **Files**: `src/backend/app/models/video_source.py`, `src/backend/app/services/video_source_service.py` (new graduation logic), `src/backend/app/services/promotion_service.py` (~L100-117, remove penalty for non-discovery), `src/backend/app/scheduler/__init__.py` (register job)

### Step 6: Expand Reel Source Roster
- In `youtube_curated_channels.json`: add more shorts/clips-native channels from trusted tech creators and official brands (current enabled mix is 73 `long_form`, 36 `mixed`, 3 `shorts`; only 39 channels are shorts-capable at all)
- Keep all 36 mixed-format channels eligible for reel output
- Add more reel-focused discovery query packs (currently 5 vs 7 for videos): add `reels-apps`, `reels-hardware-unbox`, `reels-coding-tips`
- **Files**: `src/backend/app/data/youtube_curated_channels.json`, `src/backend/app/config/video_discovery.py`

### Step 7: Add Feed Diversity Caps
- Implement source diversity enforcement in tiered_feed_service.py diversity mixer:
  - **Videos**: max 2 from same channel in first 20, max 4 in first 50, no consecutive same-channel at page boundaries
  - **Reels**: max 1 from same channel in first 10, max 2 in first 20, max 4 in first 50, no back-to-back same-channel
- **Graceful degradation**: If the promoted pool has fewer distinct sources than the cap requires (e.g., <10 distinct reel channels), serve all available content with best-effort distribution — spread sources as evenly as possible instead of dropping items to enforce the cap. Log a warning when diversity constraints cannot be fully satisfied so the admin metrics surface the gap.
- Apply after tier blending, before pagination
- **Files**: `src/backend/app/services/tiered_feed_service.py`

### Step 8: Update Feed Endpoints & caught_up Semantics
- Change `inventory_state` logic: `caught_up` means "all promoted items with published_at >= now - 7 days have been paged," not just "no more rows"
- Add `window_days: 7` and `remaining_count: int` to both `/api/v1/videos/recent` and `/api/v1/videos/reels` response schemas
- Update mobile app (`blips-mobile`) to consume new fields — since pre-release, no backward compat needed
- **Ordering dependency**: The repository split has already landed in `feed_repository.dart`. The remaining mobile dependency is migrating providers/callers off deprecated `fetchFeedPage()` and onto `fetchArticlesPage()` / `fetchVideosPage()`. Land that provider migration first, then layer the new `window_days` / `remaining_count` field consumption on top of the per-surface fetch methods.
- **Files**: `src/backend/app/api/routes/videos.py`, `src/backend/app/services/tiered_feed_service.py`, `src/backend/app/schemas/` (response models), `blips-mobile/lib/features/feed/data/feed_repository.dart`, `blips-mobile/lib/features/feed/providers/feed_providers.dart`

### Step 9: Strengthen Language Filtering
- Current gap: titles <30 chars bypass langdetect (returns None, treated as safe). Transliterated Hindi in Latin script may pass.
- Keep the existing non-Latin first-pass rejection (`_has_non_latin_letters()`) and add coverage tests if needed rather than re-implementing it
- For short titles (<30 chars): combine title, description, and channel-language metadata for a stricter decision instead of auto-passing on langdetect failure
- Add a lightweight transliterated-Latin heuristic for common non-English patterns that evade both `relevanceLanguage=en` and `langdetect`
- Ensure all discovery search queries include `relevanceLanguage=en` (already present at youtube_client.py ~L639-641, verify for all code paths)
- Add `language_filtered_count` to admin metrics for monitoring
- **Files**: `src/backend/app/ingestion/language_filter.py`, `src/backend/app/ingestion/service.py`, `src/backend/app/ingestion/checkpoint_worker.py`

### Step 10: Extend Admin Metrics
- Add to `/metrics/sources` response: `7d_pool_count`, `recent_24h_count`, `curated_vs_discovery_mix`, `classification_drops` (items reclassified), `source_promotion_demotion_actions`, `language_filtered_count`
- **Files**: `src/backend/app/api/routes/metrics.py`, `src/backend/app/services/video_source_service.py`

---

## Phase B — Structural Refactor (ship after Phase A is stable)

### Step 11: Replace Daily Budget Model with Rolling Model
- Design replacement for `IngestionBudget` table (currently PK `(day, content_type)` with row-level FOR UPDATE locks):
  - Option A: Change PK to `(window_id, content_type)` where window_id is a rolling identifier, keep lock semantics
  - Option B: Remove budget table entirely, rely on per-run batch caps + per-channel daily_cap + deduplication as concurrency safety
  - Option C: Per-run token bucket — each scheduler run gets a token (e.g., "process up to 50 new items this run"), no daily accumulation
- **Recommendation**: Option B — the per-channel caps and source_url unique constraint already prevent double-ingestion. The budget table was defense-in-depth for daily targets that no longer exist.
- **Files**: `src/backend/app/models/ingestion_budget.py`, `src/backend/app/repositories/ingestion_budget_repo.py`, `src/backend/app/ingestion/service.py`

### Step 12: Redesign Checkpoint System for Rolling Progress
- Replace the temporary Phase A "reopen/reset YouTube rows each invocation" behavior with true rolling-progress semantics:
  - add explicit polling cadence fields such as `last_polled_at` / `next_eligible_at` (or equivalent) for per-feed scheduling
  - remove `list_incomplete(day_utc=day)` as the primary completion model for videos/reels
  - checkpoints track "last processed cursor/timestamp" per feed rather than "done for today"
  - checkpoint loop returns `"status": "idle"` when all feeds have been polled within their interval, not `"complete"`
- Keep checkpoint rows and cursors for resume-safety
- **Files**: `src/backend/app/models/ingestion_progress.py`, `src/backend/app/repositories/ingestion_progress_repo.py`, `src/backend/app/ingestion/checkpoint_defaults.py`, `src/backend/app/ingestion/checkpoint_loop.py`, `src/backend/app/ingestion/checkpoint_worker.py`

---

## Open Questions & Answers

### Q1: How can we more reliably identify reels?

**Current approach**: Split duration thresholds (180s in feed/ingest, 75s in discovery/checkpoint paths) plus URL `/shorts/` pattern and other positive hints.

**Recommendation**: Unify around a single positive classification rule set and avoid unvalidated orientation metadata in Phase A.

| Signal | Source | Cost |
|--------|--------|------|
| Duration ≤120s | `contentDetails.duration` (already fetched) | Free (already in API call) |
| `/shorts/` in URL | URL pattern match | Free |
| Shorts-native channel | Curated channel config | Free |
| `#shorts` in title | Title pattern match | Free |

**Proposed classification matrix**:
- \>120s → **VIDEO** (always)
- ≤120s AND any explicit Shorts signal (`/shorts/`, shorts-native channel, `#shorts`) → **REEL**
- ≤120s with reliable duration but no explicit Shorts signal → **REEL** for now (broader intake, promotion/ranking remains the quality gate)
- Missing duration AND explicit Shorts signal → **REEL**
- Missing duration AND no explicit Shorts signal → **VIDEO**

**Explicit non-goal for Phase A**: do not use `player.embedHeight` / `player.embedWidth` to downgrade short videos into VIDEO. If a later phase wants aspect-ratio-based filtering, validate the signal against a labeled sample first.

### Q2: Does YouTube approve quota increases for pre-production apps?

**Yes, but with caveats:**
- Google's quota extension request form is available to any project with a Google Cloud project
- Pre-production apps can request increases by explaining the use case, expected traffic, and compliance with YouTube ToS
- Typical approval: 50,000-100,000 units/day for legitimate use cases
- **Timeline**: Reviews take 1-4 weeks. No guarantee of approval.
- **Practical approach for now**: Start with conservative search frequencies that fit within 10,000 units/day. The 3h video / 2h reel recommendation still lands around 11,600 search units/day before duration lookups, so it needs either a quota increase or looser starting intervals.
- **Alternative**: lean on RSS for curated channel fetches and spend quota primarily on discovery plus mixed-channel duration lookups. Curated feed fetches are cheap, but mixed-channel Shorts detection is not truly free when API-based duration lookup is enabled.

### Q3: Are we compromising on quality? Can non-English content leak in?

**Quality pipeline is solid** — 4-layer defense:
1. **Ingestion**: language detection (langdetect library), deduplication (3 methods), duration classification
2. **Promotion**: min_score thresholds (0.30 videos, 0.32 reels), clickbait detection, duplicate density penalty, creator fatigue penalty
3. **Feed**: only PROMOTED items visible, duration re-check, diversity mixing
4. **Discovery gating**: subscriber/view/engagement minimums, category whitelist

**Language leak risk is LOW but non-zero:**
- YouTube API `relevanceLanguage=en` is a hint, not a hard filter — YouTube may still return non-English results
- `langdetect` works well for titles >30 chars but unreliable below that
- Transliterated Hindi (Latin script) can pass both YouTube API filter and langdetect
- Non-Latin scripts (Devanagari, Arabic, CJK) are caught by `_has_non_latin_letters()` check

**Mitigation in Step 9**: Keep the existing non-Latin first-pass rejection, tighten short-title handling, and add a lightweight transliterated-Latin heuristic. This closes the main gap without adding heavy LLM filtering.

**This plan does NOT compromise quality.** Intake is broader, but every item still passes through the same promotion quality gate. The min_score, clickbait, and duplicate penalties are unchanged. Discovery items get an additional scoring penalty (0.03-0.04) until their source channel graduates.

---

## Projected User Experience

### Content Volume (daily)

| Metric | Videos | Reels |
|--------|--------|-------|
| Raw ingested/day (curated) | ~50-80 | ~25-40 |
| Raw ingested/day (discovery) | ~30-60 | ~20-50 |
| **Total raw/day** | **~80-140** | **~45-90** |
| Promoted/day (after quality gate) | **~35-65** | **~20-45** |
| **7-day promoted pool** | **~200-450** | **~120-280** |

### Freshness

| Path | Publish → Feed | Notes |
|------|---------------|-------|
| Curated (RSS) | ~30-60 min | RSS poll 15 min + promotion 30 min + cache 45s |
| Discovery | ~2.5-4 hours | Search interval (2-3h) + ingest + promotion 30 min |
| Median age on first page | 4-12h (videos) / 2-8h (reels) | Reels skew fresher (48h recency half-life vs 72h) |

### Poll / Refresh Cadence

| Component | Interval |
|-----------|----------|
| Ingestion scheduler | 15 min |
| YouTube discovery — videos | 3 hours initially → 60 min after quota increase |
| YouTube discovery — reels | 2 hours initially → 30-45 min after quota increase |
| Promotion scoring | 30 min |
| Feed cache TTL | 45 seconds |

### Scroll Depth Before "Caught Up"

| Usage Pattern | Videos | Reels |
|---------------|--------|-------|
| Checks once daily | ~35-65 new | ~20-45 new |
| Checks 2-3x daily | ~12-25 per session | ~8-18 per session |
| Binges full pool | 200-450 before caught up | 120-280 before caught up |

### Feed Diversity (First Screens)

- **Videos first 20**: max 2 per channel → ≥10 distinct channels, no consecutive same-channel
- **Reels first 10**: max 1 per channel → 10 distinct channels, no back-to-back

---

## Relevant Files

| File | Purpose |
|------|---------|
| `src/backend/app/core/config.py` | REEL_MAX_DURATION_SECONDS, DAILY_TARGET_*, VIDEOS_FRESH_PUBLISHED_HOURS |
| `src/backend/app/core/youtube_quota.py` | API quota budgets, search cooldowns |
| `src/backend/app/integrations/youtube_client.py` | YouTube API calls, shorts detection, duration parsing |
| `src/backend/app/ingestion/service.py` | Daily stop conditions, reel classification, language filtering |
| `src/backend/app/ingestion/checkpoint_defaults.py` | Auto-pause guardrail, daily feed defaults |
| `src/backend/app/ingestion/checkpointing.py` | Scheduled checkpoint runner entrypoint |
| `src/backend/app/ingestion/checkpoint_loop.py` | Daily completion semantics |
| `src/backend/app/ingestion/checkpoint_worker.py` | Per-feed ingestion, language check |
| `src/backend/app/ingestion/language_filter.py` | langdetect, non-Latin detection |
| `src/backend/app/repositories/ingestion_progress_repo.py` | Progress row completion / eligibility logic |
| `src/backend/app/services/promotion_service.py` | Scoring weights, discovery lane penalty, quality gates |
| `src/backend/app/services/video_source_service.py` | Source health metrics, promotion/demotion |
| `src/backend/app/services/video_discovery_service.py` | Discovery channel gating, quality thresholds |
| `src/backend/app/services/tiered_feed_service.py` | Tier blending, diversity mixer, duration filter |
| `src/backend/app/api/routes/videos.py` | Feed endpoints, inventory_state, caught_up |
| `src/backend/app/api/routes/metrics.py` | Admin metrics |
| `src/backend/app/models/ingestion_budget.py` | Daily budget model (Phase B target) |
| `src/backend/app/models/video_source.py` | VideoSourceProfile, status/graduation fields |
| `src/backend/app/data/youtube_curated_channels.json` | Curated channel roster (112 channels) |
| `src/backend/app/config/video_discovery.py` | Discovery query packs |
| `src/backend/app/scheduler/__init__.py` | Job registration, intervals |
| `tools/reclassify_reels.py` | Existing reclassification script |
| `blips-mobile/lib/features/feed/data/feed_repository.dart` | Mobile feed consumption |

---

## Verification Checklist

1. **Discovery active**: With `YOUTUBE_CURATED_ONLY=false` and `YOUTUBE_DISCOVERY_ENABLED=true`, verify discovery search runs on schedule and returns candidates
2. **No daily cap blocking**: Ingest >60 videos and >40 reels in a single day without ingestion stopping
3. **Reel reclassification**: After changing threshold to 120s, verify no 121-180s items appear in reel feed; verify reclassify script converts existing items
4. **Reel classification consistency**: Verify the 120-second rule is applied consistently across discovery, checkpoint, direct ingest, and feed filters; verify 121-180s items are absent from reels and missing-duration items require an explicit Shorts signal
5. **Auto-pause removed**: Verify channels with low conversion are demoted (lower promotion multiplier) not paused (excluded from ingestion)
6. **Graduation works**: Create a mock discovery channel with good metrics → verify it auto-promotes to rotation after threshold met, with 7-day cooldown
7. **Diversity caps**: Page through first 20 videos → verify max 2 from any channel; first 10 reels → max 1 from any channel
8. **caught_up correct**: Page through entire 7-day pool → verify caught_up only fires after last promoted item in window
9. **Language filtering**: Submit a Hindi-title video and a short (<30 char) transliterated non-English title → verify both are rejected without regressing valid short English titles
10. **YouTube quota**: Monitor actual YouTube API quota usage over 24h → verify search plus duration traffic stays within the configured real-unit budget
11. **Admin metrics**: Verify `/metrics/sources` includes 7d_pool_count, curated_vs_discovery_mix, language_filtered_count

---

## Decisions

- App is pre-release: no backward compatibility constraints on API changes or mobile updates
- No phased rollout/feature flags needed — ship Phase A as a unit, fix issues directly
- Articles stay on current daily budgeting model (unchanged)
- One week = strict rolling 7×24h window based on `published_at`
- Higher YouTube API usage acceptable if it improves supply — but must fit within actual quota (request increase proactively)
- Start with conservative search intervals (3h video, 2h reel) and tighten after quota increase approval
