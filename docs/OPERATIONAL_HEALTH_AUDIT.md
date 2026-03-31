# Operational Health Audit

Audit date: March 31, 2026  
Primary live target: `https://api.blips.tech`  
Scope: backend runtime health, endpoint freshness, scheduler/ingestion wiring, mobile runtime refresh behavior, deployment sanity, and operational blind spots.

Remediation update: March 31, 2026

- Fixed in repo before Render-side follow-up:
  - `/health` now includes scheduler and ingestion runtime status and degrades when worker signals look unhealthy
  - `/ops/status` DB/Redis pool stats now use the real shared engine and Redis pool
  - tiered-feed cache hits now preserve the original cache generation timestamp
  - session playlist headers and JSON freshness fields now describe the same returned page slice
  - worker cadence is explicit in `render.yaml` via `INGESTION_SCHEDULER_MINUTES=15`
  - mobile tab entry now triggers silent refresh for Articles, Videos, and Reels
  - article image prefetch failures are now non-fatal during runtime navigation
- Still requires Render verification after deploy:
  - current worker restarts / OOM history
  - live reels recent-refresh underfill
  - live `/health` and `/ops/status` output after rollout

## 1. Executive Summary

Overall operational status: partially healthy.

- The production-like backend is up, accepts anonymous mobile sessions, and serves non-empty Articles, Videos, and Reels feeds.
- Articles and Videos are operationally healthy based on live feed responses and public inventory-health data.
- Reels are functional but currently below the app’s own freshness policy: public inventory health reported `is_healthy=false` for reels at `2026-03-31T19:09:03Z`, specifically `Recent refresh below minimum: 8 < 12 in last 24h`.
- The original release blockers were observability gaps and mobile refresh wiring. Those are now fixed in code and need redeploy verification.

Release confidence level: Medium.

- User-facing feed delivery is working.
- Operational confidence is now mainly reduced by live reels freshness lag and the lack of direct Render restart/OOM evidence in this workspace.

Top current operational risks:

- Live reels freshness is still below target and needs Render-side investigation.
- Reels freshness is lagging behind the configured minimum recent-refresh threshold.
- Current restart / OOM / CPU evidence is still unavailable until Render logs are inspected.

## 2. Backend Runtime Health

### Health Check Quality

Code path: `src/backend/app/main.py`

- `/health` checks:
  - PostgreSQL reachability via `SELECT 1`
  - Redis reachability via `PING`
- `/health` does not check:
  - scheduler leadership
  - worker liveness
  - last successful ingestion time
  - queue/backlog state
  - recent content freshness by surface

Live evidence:

- `GET https://api.blips.tech/health` returned `200` with `{"status":"healthy","database":"ok","redis":"ok"}` during this audit.
- At the same time, `GET https://api.blips.tech/api/v1/inventory/health` reported overall `is_healthy=false` due to reels freshness. That proves `/health` is only a dependency liveness check, not a meaningful operational readiness check.

Assessment: health check is too shallow for production operations.

### Scheduler Status

Code paths:

- `src/backend/app/worker.py`
- `src/backend/app/scheduler/__init__.py`
- `render.yaml`

What is correct:

- Production is correctly split into API and worker services.
- `render.yaml` sets:
  - API: `SCHEDULER_ENABLED=false`, `INGESTION_ENABLED=false`
  - Worker: `SCHEDULER_ENABLED=true`, `INGESTION_ENABLED=true`
- Worker leadership uses a Redis lock with refresh and safe reacquire logic.

What is risky:

- The worker logs `NEWS_FETCH_INTERVAL_MINUTES`, but the scheduler actually resolves cadence through `_resolve_ingestion_minutes()`.
- In `render.yaml`, the worker sets `NEWS_FETCH_INTERVAL_MINUTES=30`.
- In `src/backend/app/scheduler/__init__.py`, the legacy fallback is clamped to `5..15` minutes. That means the effective scheduler cadence is `15 minutes`, not `30 minutes`.

Assessment:

- Scheduler ownership is designed correctly.
- Scheduler cadence is operationally confusing and currently mismatched between env intent, worker logs, and effective runtime behavior.

### Ingestion Status

Code paths:

- `src/backend/app/scheduler/tasks_ingestion.py`
- `src/backend/app/ingestion/checkpointing.py`
- `src/backend/app/services/content_event_dispatcher.py`

What is correct:

- Ingestion runs through checkpointed ingestion.
- Immediate AI summarization runs after fetch as a freshness accelerator.
- Promotion runs immediately after ingestion.
- Content-event dispatch invalidates feed caches after readiness changes.
- Checkpointing invalidates inventory health and tiered feed caches after ingestion.

Live evidence strongly suggests ingestion is active:

- Articles inventory:
  - newest published at `2026-03-31T18:12:47`
  - newest created at `2026-03-31T18:15:37`
  - `fresh_count=193`
- Videos inventory:
  - newest published at `2026-03-31T18:22:57`
  - newest created at `2026-03-31T18:33:17`
  - `fresh_count=105`
- Reels inventory:
  - newest published at `2026-03-31T16:01:00`
  - newest created at `2026-03-31T18:13:18`
  - `fresh_count=107`
  - recent-refresh policy failing (`8 < 12` in last `24h`)

Assessment:

- Ingestion is not stalled globally.
- Reels are ingesting, but the recent-refresh lane is underperforming against the configured threshold.

### Cache Freshness

Code paths:

- `src/backend/app/services/tiered_feed_service.py`
- `src/backend/app/services/inventory_service.py`
- `src/backend/app/services/content_event_dispatcher.py`

What is correct:

- Tiered feed cache TTL is `45s`.
- Inventory health cache TTL is `60s`.
- Feed caches are invalidated after checkpointing, top-up completion, admin content changes, and content-event dispatch.

Live evidence:

- Repeated requests to direct feed endpoints changed from `MISS` to `HIT` as expected.
- Feed versions remained stable across repeated cached reads.

Operational problems found:

- On a tiered feed cache hit, `X-Feed-Generated-At` is set to the current request time, not the original cache-generation time. This was proven live:
  - same `X-Feed-Version`
  - same top item IDs
  - repeated `HIT`
  - different `X-Feed-Generated-At`
- That makes cache age look fresher than it really is.

Assessment: cache behavior is functionally correct, but feed freshness diagnostics are partly misleading.

### DB / Redis Connectivity

Live evidence:

- `/health` returned DB `ok` and Redis `ok`.

Code-level issue:

- `src/backend/app/core/observability.py` is broken for pool diagnostics:
  - `get_db_pool_stats()` imports `app.core.database`, which does not exist.
  - `get_redis_pool_stats()` imports `get_redis_pool`, which is not exported from `app.core.dependencies`.

Local proof:

- Calling those helpers returned:
  - `{'status': 'error', 'error': "No module named 'app.core.database'"}`
  - `{'status': 'error', 'error': "cannot import name 'get_redis_pool' ..."}`

Assessment:

- Core DB/Redis connectivity works.
- Pool observability is currently broken.

### Latest Content Freshness By Type

Live probe highlights from March 31, 2026:

- Articles:
  - `/api/v1/articles/recent?limit=5`
  - newest published in response: `2026-03-31T17:59:12`
  - newest created in response: `2026-03-31T18:15:05.336657`
  - inventory health newest published: `2026-03-31T18:12:47.194962`
- Videos:
  - `/api/v1/videos/recent?limit=5`
  - newest published in response: `2026-03-31T17:02:22`
  - newest created in response: `2026-03-31T17:43:15.394771`
  - inventory health newest published: `2026-03-31T18:22:57`
- Reels:
  - `/api/v1/videos/reels?limit=10`
  - newest published in response: `2026-03-31T16:01:00`
  - newest created in response: `2026-03-31T18:13:18.612785`
  - inventory health also shows newest published `2026-03-31T16:01:00`

Assessment:

- Articles: fresh
- Videos: fresh
- Reels: available but lagging compared to configured recent-refresh expectations

### Restarts / Resource Issues Found

Direct current-runtime proof is missing here.

- I did not have Render log access or an admin key for protected operational endpoints.
- I could not directly confirm restart counts, OOM kills, CPU pressure, or health-check flapping from production logs.

Code and config signals:

- API and worker both run on Render `starter`.
- Worker is doing scheduler + ingestion + clustering + promotion + AI retry on the same service.
- Effective ingestion cadence is 15 minutes, not the 30 minutes implied by env naming.

Assessment:

- No direct proof of current restarts or OOMs.
- There is clear configuration pressure risk, especially on the worker.

## 3. Log Audit

Direct Render log evidence was not available in this workspace, so I could not truthfully list current recurring production log lines.

I did audit the code paths that would generate operational warnings and silent failures.

### Important warnings already instrumented in code

- Worker Redis lock loss / reacquire warnings in `src/backend/app/worker.py`
- Ingestion stall warning in `src/backend/app/scheduler/tasks_health.py`
- AI cap reached warnings in `src/backend/app/scheduler/tasks_ai_retry.py`
- RSS / YouTube fetch timeout logs in `src/backend/app/ingestion/service.py`
- YouTube quota and discovery warnings in `src/backend/app/core/youtube_quota.py` and `src/backend/app/ingestion/signals/*.py`

### Silent failure patterns and swallowed-error risks

- `src/backend/app/services/topup_service.py`
  - request-triggered top-up check failures are converted into warnings and `False`
  - this can suppress evidence that top-up triggering is not happening
- `src/backend/app/scheduler/tasks_ai_retry.py`
  - per-item exceptions are accumulated and processing continues
  - good for resilience, but easy to under-notice if logs are not aggregated
- `src/backend/app/ingestion/service.py`
  - multiple extraction and summarization failures degrade to warnings and fallback behavior
  - operationally acceptable, but it increases the chance of quality regressions without hard failures
- `src/backend/app/main.py`
  - health alerts can fail to send without failing `/health`

### Suspicious retry / timeout patterns

- Worker Redis lock refresh tolerates repeated failures before shutdown.
- Mobile client retries 5xx, 429, and timeout errors automatically.
- Ingestion paths already log hard timeouts for RSS (`300s`) and YouTube (`180s`).

Assessment:

- The logging surface is broad enough to diagnose issues if logs are collected.
- The current risk is not “missing log statements”; it is limited current visibility and several soft-failure paths that do not fail closed.

## 4. Endpoint Freshness Audit

### Articles

Routes verified:

- `/api/v1/articles/recent`
- `/api/v1/session/playlist?type=ARTICLE`

Findings:

- Route exists and returns coherent data.
- Live item counts were non-zero.
- Freshness fields and tiering were populated.
- Cache headers were present.
- Direct endpoint freshness looked healthy.

### Videos

Routes verified:

- `/api/v1/videos/recent`
- `/api/v1/session/playlist?type=VIDEO`

Findings:

- Route exists and returns coherent data.
- Live item counts were non-zero.
- Freshness fields and tiering were populated.
- Direct endpoint freshness looked healthy.

### Reels

Route verified:

- `/api/v1/videos/reels`

Findings:

- Route exists and returns coherent data.
- Live item counts were non-zero.
- Freshness tiering and feed-version fields were populated.
- Inventory health reports reels as operationally degraded due to recent-refresh underfill.

### Response metadata / cache behavior

What is working:

- `X-Cache`, `X-Feed-Source`, `X-Feed-Version`, `X-Newest-*` are present on direct feed endpoints.
- Cache transitions from `MISS` to `HIT` behave as expected.

Problems found:

- `X-Feed-Generated-At` on direct tiered feeds is not a true cache-generation timestamp on cache hits.
- Session playlist body freshness metadata is snapshot-wide, while headers are page-slice-wide.

This was visible live on session playlist endpoints:

- `session_articles` body `newest_published_at`: `2026-03-31T18:05:13`
- `session_articles` header `X-Newest-Published-At`: `2026-03-31T17:59:12`

Assessment:

- Endpoints are serving fresh enough data for Articles and Videos.
- Reels are serving coherent data but are behind freshness policy.
- Operational metadata is not fully coherent across all feed routes.

### Content counts

Public inventory-health summary at audit time:

- Articles:
  - fresh `193`
  - reservoir `1768`
- Videos:
  - fresh `105`
  - reservoir `1399`
- Reels:
  - fresh `107`
  - reservoir `483`
  - recent refresh count `8` against threshold `12`

## 5. Mobile Runtime Audit

Key code paths:

- `lib/features/feed/presentation/feed_shell_page.dart`
- `lib/features/feed/providers/feed_providers.dart`
- `lib/features/feed/data/feed_session_store.dart`
- `lib/features/feed/data/feed_repository.dart`

Validation:

- I traced the runtime code paths directly.
- I also ran the existing Flutter tests:
  - `test/unit/articles_notifier_refresh_test.dart`
  - `test/unit/videos_notifier_cache_test.dart`
  - `test/unit/reels_notifier_stability_test.dart`
  - result: all passed

### A. On app open

Does Articles fetch? Yes.

- Articles notifier loads on shell build.
- It restores a saved session if present; otherwise it shows cached articles if present; otherwise it fetches from network.
- If it restores or serves cache, it still triggers a background refresh.

Does Videos fetch? Yes.

- Same stale-while-revalidate pattern as Articles.

Does Reels fetch? Yes.

- Reels page is mounted inside a kept-alive `PageView`.
- Shell also performs a background reels warm-up after first frame.

Important nuance:

- All three surfaces can show cached or resumed data first.
- Fresh network data is usually fetched in the background, not necessarily before first paint.

### B. On app resume after 1 hour

Does it revalidate? Yes, but only for the current tab.

- `FeedShellPage._useResumeRefresh()` refreshes exactly one surface based on `currentIndex`.
- It does not refresh all three surfaces on resume.

What happens to the other tabs?

- They stay mounted and continue to have polling timers.
- Articles/videos poll every `90s`.
- Reels polls every `60s`.
- That means non-current tabs may refresh soon, but not immediately because of the resume event itself.

### C. On tab switch

Does entering a tab trigger refresh logic? No.

- A normal tab switch updates `currentIndex`.
- It does not call `refreshSilently()` for the newly entered tab.
- Only a tab re-tap triggers manual refresh logic.

Operational impact:

- A user can switch into a stale tab and see older local/session data until the next poll or a manual refresh action.

### D. On stale cache

Does the app detect stale cache and refresh automatically? Partially.

- The app does stale-while-revalidate, so cached data is followed by background refresh attempts.
- Session restore windows are long:
  - Articles soft restore: `72h`
  - Videos soft restore: `48h`
  - Reels soft restore: `24h`
- Remote continuation is tighter:
  - Articles/videos: `1h`
  - Reels: `6h`

Operational impact:

- Stale local cache can mask backend freshness briefly at startup.
- Stale non-current tabs can persist longer than intended because tab-entry itself is not a refresh trigger.

### Stale-while-revalidate status

Assessment: implemented and functioning, but not complete.

- Good:
  - cached data is followed by background refresh
  - pending new content detection is based on `feed_version`
  - notifier tests cover manual refresh and silent refresh stability
- Gap:
  - no explicit “refresh on tab enter”
  - resume only refreshes the visible tab

## 6. Root Causes / Risks

### Stale data risks

- Reels recent-refresh lane is under target in live inventory health.
- Mobile may show resumed or cached data first, especially on startup.
- Non-current tabs are not refreshed on entry.

### Refresh gaps

- `/health` does not expose worker or ingestion health.
- App resume refreshes only the visible tab.
- Normal tab switch does not trigger refresh.

### Silent errors

- Top-up trigger failures are softened into warnings.
- Multiple ingestion and AI retry paths continue after exceptions.
- This is resilient, but it can hide degraded freshness unless logs are reviewed.

### Unstable playback / runtime flows

- I did not find evidence of a current production playback outage.
- Existing reels/video notifier tests passed.
- Residual risk remains around stale content visibility more than player stability.

### Bad cache invalidation

- Cache invalidation hooks exist and appear wired correctly after ingestion/top-up/content-ready events.
- The bigger problem is not invalidation itself, but misleading freshness metadata on cached responses.

### Scheduler not running when assumed

- I found no evidence of a total scheduler stall.
- I did find that scheduler cadence is not what the Render env naming suggests.

### Service restarts / resource constraints

- I could not directly prove current restart or OOM events without Render logs.
- Worker plan size plus effective 15-minute cadence is a real operational risk to watch.

## 7. Recommended Fixes

### Critical

- Address reels recent-refresh underfill.
  - The live system is serving reels, but it is below the backend’s own freshness target.

### High

- Deploy and verify the completed observability/runtime fixes.
  - `/health` now includes scheduler/ingestion status.
  - `/ops/status` pool visibility is fixed.
  - Direct feed and session freshness metadata are now coherent.
  - Mobile tab entry now triggers refresh.
  - Production cadence is explicit in `render.yaml`.

### Medium

- Protect or intentionally document public operational inventory endpoints.
  - `/api/v1/inventory/health` is publicly readable even though operational docs frame inventory-health diagnostics as protected.
- Add a post-deploy operational smoke check.
  - Run `/health`, inventory health, anonymous session bootstrap, articles/videos/reels feeds, and freshness/header checks automatically.
- Add alerting on public inventory health degradation by surface.
  - Reels degradation was externally visible even while `/health` stayed green.
- Add automated validation for `/ops/status`.
  - This endpoint is too important to remain untested.

## Test / Automation Gap Analysis

Current gaps:

- No enforced post-deploy smoke test for public feed freshness.
- No automated check for stale `reels` recent-refresh counts.
- No Render-log automation for restart / OOM regression detection.

Added artifact:

- `src/backend/scripts/operational_check.py`
  - creates an anonymous session
  - probes health and feed endpoints
  - captures counts, freshness timestamps, cache headers, and anomalies
  - can optionally probe admin endpoints when `ADMIN_API_KEY` is supplied

## Bottom Line

What is working:

- backend is up
- DB and Redis are reachable
- anonymous session auth works
- articles/videos/reels endpoints return coherent data
- articles and videos are fresh enough
- mobile stale-while-revalidate wiring exists and is tested

What is broken:

- live reels recent-refresh is still below target
- Render restart / OOM evidence is still unverified from this workspace

What is stale:

- reels recent-refresh supply is below the configured threshold

What is not being triggered as expected:

- app resume does not immediately refresh non-current tabs
