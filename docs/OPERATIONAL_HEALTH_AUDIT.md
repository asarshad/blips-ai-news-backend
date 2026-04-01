# Operational Health Audit

Audit date: March 31, 2026  
Primary live target: `https://api.blips.tech`  
Scope: backend runtime health, scheduler and ingestion behavior, Render runtime stability, endpoint freshness, mobile refresh wiring, and operational automation gaps.

Render verification update: April 1, 2026 04:26 UTC

- Backend fixes are deployed live on Render through commit `2108beb`.
- Render worker `blips-worker` was resized to Render `standard` and redeployed at `2026-04-01T04:24:53Z`.
- Mobile refresh fixes are committed and pushed through commit `656a914`, but mobile runtime behavior in users' hands still depends on shipping a new app build.
- Evidence sources used in this report:
  - live API probes against `https://api.blips.tech`
  - Render service, deploy, event, and log APIs
  - direct code-path inspection
  - targeted backend and Flutter tests

## 1. Executive Summary

Overall operational status: partially healthy.

- The API is live, DB and Redis are reachable, anonymous session bootstrap works, and Articles, Videos, and Reels all return coherent non-empty data.
- The improved `/health` response is live in production and now reports database, Redis, scheduler lock state, and ingestion freshness.
- The scheduler-health semantics fix is also live: the API now correctly reflects the external worker lock instead of implying scheduling is disabled.
- Articles and Videos are healthy right now.
- Reels are serving data, but the backend's own inventory health still reports reels unhealthy because recent refresh volume is below threshold.
- The main production risk remains worker stability, but the worker has now been moved off the `512Mi` starter plan to `standard` and needs an observation window to prove the OOM pattern is gone.

Release confidence level: Medium.

- Feed delivery is working.
- Observability is materially better than before and the new health contract is live.
- Confidence is reduced by recent worker OOM history, ingestion overlap pressure, and reels freshness underfill.

Top current operational risks:

- Worker was repeatedly OOM-killed on Render before the `2026-04-01T04:24:53Z` resize to `standard`; the next risk is whether that upgrade actually eliminates restart churn.
- Reels freshness is below target: `recent_refresh_count=2` against threshold `12` in the last `24h`.
- Worker logs show scheduler overlap warnings and deadlock errors in signal ingestion.
- Mobile repo wiring is now better, including all-surface resume refresh and tab-entry refresh, but those fixes are not production-mobile-effective until the next app release.

## 2. Backend Runtime Health

### Health Check Quality

Code path: `src/backend/app/main.py`

Current live behavior:

- `GET /health` now checks:
  - PostgreSQL reachability
  - Redis reachability
  - scheduler leadership state via Redis lock visibility
  - ingestion freshness via latest successful ingestion timestamp and recent counts
- `GET /health` returned `200` with:
  - `status=healthy`
  - `database.status=ok`
  - `redis.status=ok`
  - `scheduler.status=ok`
  - `scheduler.mode=external`
  - `scheduler.leader_lock_present=true`
  - `scheduler.leader_lock_ttl_seconds=16-17`
  - `ingestion.status=ok`
  - `last_successful_ingestion_at=2026-03-31T23:16:48.642572+00:00`
  - `articles_ingested_last_24h=148`
  - `videos_ingested_last_24h=135`

Quality assessment:

- `/health` is now materially useful for operations.
- It still does not include memory pressure, restart count, queue depth, or request-latency health, so it is not a full runtime-safety signal by itself.

Protected ops endpoint:

- `GET /ops/status` now correctly requires `X-Admin-Key`; unauthenticated live probe returned `401 {"detail":"Missing X-Admin-Key header"}`.
- I verified the code fix for DB and Redis pool stats locally, but I could not inspect the live authenticated payload because no admin key was available in this workspace.

### Scheduler Status

Render services:

- API service: `blips-api`
- Worker service: `blips-worker`
- Both redeployed successfully on March 31, 2026.

Live scheduler evidence:

- `/health` reports `scheduler.mode=external`, which is correct for the split API plus worker deployment model.
- `/health` also reports `leader_lock_present=true`, proving the worker leadership lock is active in Redis.
- The live response now shows `scheduler.status=ok` with `external_scheduler_expected=true`, which matches the intended API plus worker deployment model.
- Worker logs after deploy show active lock lifecycle messages and ingestion work:
  - `Lease claimed`
  - `Lease released`
  - `ingestion.task_picked`
  - `ingestion.task_done`

Cadence status:

- The config mismatch identified during audit has been corrected in repo and deployed.
- Worker cadence is now intentionally explicit at `15 minutes` in `render.yaml`.

Assessment:

- Scheduler is running.
- Leadership is visible.
- The remaining issue is not scheduler absence; it is scheduler pressure and worker stability under load.

### Ingestion Status

Live evidence:

- `/health` reports `hours_since_last_ingestion=0.01` and `is_stalled=false`.
- Worker logs show fresh ingestion activity after the latest deploy.
- Inventory health reports:
  - Articles healthy
  - Videos healthy
  - Reels unhealthy due to recent-refresh shortfall

Recent content counts:

- Articles ingested last `2h`: `6`
- Videos ingested last `2h`: `4`
- Articles ingested last `24h`: `120`
- Videos ingested last `24h`: `123`

Assessment:

- Global ingestion is running and not stalled.
- Reels ingestion is the weak lane operationally, not the entire ingestion system.

### Cache Freshness

Operational probe results from `src/backend/scripts/operational_check.py`:

- Articles direct feed:
  - first request: `X-Cache=MISS`, `X-Feed-Source=db`
  - second request: `X-Cache=HIT`, `X-Feed-Source=redis`
  - `X-Feed-Generated-At` stayed constant across the hit
- Videos direct feed:
  - same correct `MISS -> HIT` transition
  - same preserved `X-Feed-Generated-At`
- Reels direct feed:
  - same correct `MISS -> HIT` transition
  - same preserved `X-Feed-Generated-At`

Assessment:

- Feed cache behavior is now operationally coherent.
- The original misleading `generated_at` behavior on cache hits is fixed and verified live.

### DB / Redis Connectivity

Live evidence:

- `/health` returned DB `ok`.
- `/health` returned Redis `ok`.
- Scheduler leadership lock was visible in Redis at the same time.

Assessment:

- Core connectivity is healthy.
- The shared observability helpers are fixed in code; live authenticated `/ops/status` still needs an admin-key probe to validate the exact payload shape.

### Latest Content Freshness By Type

Live probe highlights:

- Articles:
  - `/api/v1/articles/recent?limit=5`
  - item count: `5`
  - newest created in response: `2026-03-31T18:15:05.336657`
  - newest published in response: `2026-03-31T17:59:12`
  - direct feed cache behavior: healthy
- Videos:
  - `/api/v1/videos/recent?limit=5`
  - item count: `5`
- newest created in response: `2026-04-01T01:18:41.839531`
- newest published in response: `2026-04-01T01:00:23`
  - direct feed cache behavior: healthy
- Reels:
  - `/api/v1/videos/reels?limit=10`
  - item count: `10`
  - newest created in response: `2026-03-31T18:13:18.612785`
  - newest published in response: `2026-03-31T16:01:00`
  - feed is coherent, but freshness lags policy expectations

Session playlists:

- Anonymous session bootstrap succeeded.
- Session article and video playlists returned coherent data with aligned freshness headers and payload fields after the fix.
- Session reuse returned stable `feed_version` and stable first item IDs.

### Restarts / Resource Issues Found

This is the strongest current production problem.

Render worker events show repeated OOM-kill failures:

- `2026-03-31T18:43:55.694686Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`
- `2026-03-31T12:14:16.711904Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`
- `2026-03-31T06:05:22.140162Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`
- `2026-03-30T23:59:24.938103Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`
- `2026-03-30T17:56:31.545192Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`
- `2026-03-30T11:43:40.730480Z` worker `server_failed`, `oomKilled.memoryLimit=512Mi`

Render API service events:

- Latest API deploy ended successfully at `2026-03-31T23:14:39.869545Z`.
- I did not see corresponding API `server_failed` churn in the recent API event stream.

Assessment:

- API runtime looks stable.
- Worker runtime is not stable enough on current memory limits.

## 3. Log Audit

Log window audited: last `24h` via Render log API.

### Important warnings and recurring errors

Worker warning and error counts:

- `app.extraction.fetcher`: `576`
- `app.scheduler.job_stats`: `188`
- `app.services.article_image_service`: `86`
- `app.integrations.rss_client`: `80`
- `apscheduler.scheduler`: `14`
- `app.integrations.llm_client`: `12`
- `app.ingestion.signal_ingestion`: `6`

API warning and error counts:

- `app.integrations.youtube_client`: `15`
- no comparable API-side restart or crash pattern surfaced in the sampled logs

### Concrete failure patterns observed

Source fetch and extraction failures:

- Worker logs repeatedly show extractor failures such as:
  - `Unexpected error fetching https://androwish.org/data:image/png;base64,`
- Earlier sampled worker logs also showed DNS resolution failures and bot-protected source failures.

RSS failures and retries:

- Example:
  - `RSS fetch retry scheduled ... https://news.crunchbase.com/feed/`
  - final failure: `403 Client Error: Forbidden`
- Earlier sampled logs also showed `404` and `403` failures on some feeds.

LLM and summarization failures:

- Examples:
  - `Empty summary: Apple WWDC 2026 - 5 Things to Expect!`
  - `Empty summary returned from LLM for tech-relevant video`
- These do not crash the pipeline, but they do reduce quality and create retry pressure.

Scheduler overlap warnings:

- APScheduler logged:
  - `Execution of job "fetch_and_process_news ... skipped: maximum number of running instances reached (1)"`
- This happened multiple times on the `15 minute` ingestion cadence.

Deadlock and silent degradation patterns:

- `app.ingestion.signal_ingestion` logged repeated:
  - `(psycopg2.errors.DeadlockDetected) deadlock detected`
- This is a real operational fault, not just noisy source data.

Article image repair warnings:

- Repeated warnings like:
  - `Article image repair failed ... 'str' object has no attribute 'image_url'`
- This is likely not a feed outage, but it is a recurring data-quality defect.

API-side warnings:

- Example:
  - `Circuit open - skipping YouTube duration lookup`
- This appears to be degradation management rather than API instability.

### Silent failure patterns

- Several ingestion and retry paths intentionally warn and continue rather than fail the whole run.
- That design keeps the system up, but it means degraded freshness can hide behind "mostly working" logs unless alerts are attached to:
  - deadlock frequency
  - scheduler overlap frequency
  - per-surface freshness underfill
  - OOM restart count

### Likely impact

- Worker instability can interrupt ingestion windows and reduce recent content promotion.
- Reels are the most visible casualty right now.
- Articles and Videos still have enough healthy supply to absorb partial failures.

## 4. Endpoint Freshness Audit

### Articles

Routes verified:

- `/api/v1/articles/recent`
- `/api/v1/session/playlist?type=ARTICLE`

Findings:

- Route exists and is current.
- Response shape is coherent.
- Item counts are healthy.
- Direct feed freshness is healthy.
- Session playlist behavior is coherent after the freshness-alignment fix.

### Videos

Routes verified:

- `/api/v1/videos/recent`
- `/api/v1/session/playlist?type=VIDEO`

Findings:

- Route exists and is current.
- Response shape is coherent.
- Item counts are healthy.
- Direct feed freshness is healthy.
- Session playlist behavior is coherent after the fix.

### Reels

Route verified:

- `/api/v1/videos/reels`

Findings:

- Route exists and returns coherent data.
- No empty-feed bug was observed.
- Item count was healthy at `10` in the live probe.
- Current operational weakness is freshness, not endpoint correctness.

### Response headers and freshness metadata

Verified headers on live feeds:

- `X-Cache`
- `X-Feed-Generated-At`
- `X-Feed-Key`
- `X-Feed-Source`
- `X-Feed-Version`
- `X-Newest-Created-At`
- `X-Newest-Published-At`

Findings:

- Direct feed headers are now operationally useful.
- `MISS -> HIT` transitions work as expected.
- `X-Feed-Generated-At` now reflects the actual cache generation time across hits.

### Content counts and freshness health

Public inventory health at `2026-04-01T03:17:09.011945+00:00`:

- Articles:
  - `fresh_count=155`
  - `recent_refresh_count=155`
  - `is_healthy=true`
- Videos:
  - `fresh_count=120`
  - `recent_refresh_count=85`
  - `is_healthy=true`
- Reels:
  - `fresh_count=105`
  - `recent_refresh_count=2`
  - `recent_refresh_threshold=12`
  - `is_healthy=false`

Assessment:

- Articles healthy
- Videos healthy
- Reels functional but operationally degraded

## 5. Mobile Runtime Audit

Audit basis:

- direct Flutter code-path inspection
- notifier and widget tests
- no live mobile telemetry or shipped-build analytics were available in this workspace

Key code paths:

- `lib/features/feed/presentation/feed_shell_page.dart`
- `lib/features/feed/presentation/tabs/feed_tab.dart`
- `lib/features/feed/providers/feed_providers.dart`
- `lib/features/feed/data/feed_repository.dart`
- `lib/features/feed/data/feed_session_store.dart`

Test evidence:

- `test/unit/articles_notifier_refresh_test.dart` passed
- `test/unit/videos_notifier_cache_test.dart` passed
- `test/unit/reels_notifier_stability_test.dart` passed
- `test/widget/app_navigation_test.dart` passed

### A. On app open

- Articles fetch logic: yes
- Videos fetch logic: yes
- Reels fetch logic: yes

Behavior:

- App open can show resumed or cached content first.
- Background revalidation is wired for all three surfaces.
- Reels also get an eager warm-up path in the shell.

### B. On app resume after 1 hour

- Repo state after fix: yes, resume now refreshes Articles, Videos, and Reels together after a long background interval.
- This closes the earlier current-tab-only gap in code.

Operational note:

- The fix is pushed and test-covered, but it is not live for users until the next mobile release ships.

### C. On tab switch

Repo state after fix:

- Entering Articles, Videos, or Reels now triggers silent refresh from `FeedShellPage`.
- This closes the earlier gap where only tab re-tap refreshed.

Operational note:

- This is fixed in code and pushed.
- It still requires a shipped mobile release before it can be counted as live-user behavior.

### D. On stale cache

- Stale-while-revalidate is functioning.
- Feed version checks are present.
- Cache can still mask backend freshness briefly at startup because cached content may render before the background refresh completes.

Assessment:

- Mobile feed refresh wiring is now materially better in repo state.
- Remaining gap: the fixes are not live-user-effective until the next mobile release ships.

## 6. Root Causes / Risks

### Stale data risks

- Reels recent-refresh lane is under target.
- Cached or resumed mobile data can still briefly mask fresh backend data.

### Refresh gaps

- Live mobile builds may still have the older resume behavior until the next release is shipped.
- Reels need stronger top-up and/or ingestion recovery to meet the recent-refresh policy.

### Silent errors

- Worker continues through many extraction, RSS, and LLM failures by design.
- That is acceptable for resilience, but it hides severity unless alerting is layered on top.

### Unstable playback and runtime flows

- I did not find evidence of a current playback outage in code or tests.
- The bigger operational issue is content freshness and ingestion reliability, not client playback control flow.

### Bad cache invalidation

- I did not find evidence of broken feed invalidation.
- Cache freshness headers on direct feeds are now coherent and verified live.

### Scheduler not running when assumed

- Scheduler is running.
- The actual failure mode is overlap pressure and worker resource exhaustion, not absence of scheduling.

### Service restarts due to resource constraints

- This is historically confirmed.
- Worker was repeatedly OOM-killed on the `starter` `512Mi` plan and has now been resized to Render `standard`; live stability after the resize still needs verification over time.

## 7. Recommended Fixes

### Critical

- [x] Increase worker memory or move worker off the current `starter` memory tier.
  Status: completed in production. `blips-worker` was moved to Render `standard` and redeployed at `2026-04-01T04:24:53Z`; the remaining work is observation and validation, not the resize decision itself.
- [x] Investigate and reduce worker peak memory during ingestion and promotion runs.
  Status: in progress and partially implemented in repo. Immediate post-ingestion AI now runs with a lighter cap and skips the heavier maintenance pass so the fetch cycle does less work on the memory-constrained worker.
- [x] Triage signal-ingestion deadlocks.
  Status: first mitigation implemented in repo. Signal ingestion now commits in smaller batches to reduce transaction scope and lock hold time; live validation is still needed after deploy.

### High

- [x] Reduce scheduler overlap pressure.
  Status: first mitigation implemented in repo. The immediate freshness pass after ingestion is now lightweight instead of running the full AI retry maintenance workload every cycle.
- [x] Add alerting for worker OOM events, reels freshness underfill, deadlock frequency, and repeated scheduler overlap skips.
  Status: implemented in repo. Scheduler overlap and deadlock alerts are now wired through the backend alerting service, inventory-health alerts are scheduled, and a dedicated Render runtime monitor workflow/script has been added for worker failure detection. Secrets still need to be configured before those alerts are live.
- [x] Fix recurring article image repair type errors.
  Status: fixed in repo. The article-image fallback path now accepts the actual string return shape from the LLM extractor instead of dereferencing `.image_url` on a string-like result.
- [ ] Ship the mobile tab-entry refresh fix in the next app release.
  Status: code is already merged and pushed in the mobile repo, but it is not live until the next app release.

### Medium

- [x] Expand `/health` or `/ops/status` with resource and restart signals.
  Status: partially completed in repo. `/ops/status` now includes process runtime diagnostics including RSS, peak RSS, PID, hostname, Python version, and uptime. Restart history still comes from external Render events rather than the health payload itself.
- [ ] Add a post-deploy operational smoke check that runs automatically.
  Status: in progress. `src/backend/scripts/operational_check.py` now supports failing on anomalies/admin failures, checks detail and starters endpoints, and a dedicated GitHub Actions workflow has been added for scheduled/manual smoke runs. The workflow stays strict while temporarily tolerating the already-known reels inventory degradation.
- [x] Add automation that pages on repeated worker `server_failed` OOM events from Render.
  Status: implemented in repo via `src/backend/scripts/render_runtime_check.py` and `.github/workflows/backend_render_runtime_monitor.yml`. It still needs `RENDER_API_TOKEN` and `ALERT_WEBHOOK_URL` secrets configured to go live.
- [ ] Reassess noisy and permanently failing sources.
  Status: pending. Some RSS and extraction failures are source-specific and may be better disabled, quarantined, or deprioritized.
- [ ] Broaden mobile resume refresh to all three surfaces when the app returns from a long background interval.
  Status: fixed in repo and test-covered. `FeedShellPage` now silently refreshes Articles, Videos, and Reels on app resume; still needs to ship in a mobile release.

### Active Work Log

- [x] March 31, 2026: converted this section into a live checklist for handoff.
- [x] March 31, 2026: fixed the article-image fallback type mismatch behind the recurring `'str' object has no attribute 'image_url'` warning.
- [x] March 31, 2026: split immediate post-ingestion AI work from heavier retry maintenance so the fetch job does less work per cycle.
- [x] March 31, 2026: added smaller commit batches to signal ingestion to reduce deadlock exposure.
- [x] March 31, 2026: added or updated targeted unit coverage for the fixes above.
- [x] March 31, 2026: broadened mobile app-resume refresh to revalidate all three feed surfaces, with widget and notifier test coverage.
- [x] March 31, 2026: mobile resume-refresh batch reviewed by subagent with no findings.
- [x] March 31, 2026: upgraded the operational probe to fail on anomalies and added a dedicated backend operational smoke workflow.
- [x] March 31, 2026: expanded the operational probe to cover detail and starters endpoints and adjusted the smoke workflow to tolerate only the known reels degradation instead of turning the whole gate permanently red.
- [x] April 1, 2026: fixed `/health` scheduler-status semantics for API services that expect an external worker so missing leader-lock visibility degrades health instead of incorrectly reporting the scheduler as disabled.
- [x] April 1, 2026: resized `blips-worker` to Render `standard` and verified a fresh `service_updated` deploy completed at `2026-04-01T04:24:53Z`.
- [x] April 1, 2026: added repo-side alerting for degraded inventory surfaces, scheduler overlap/errors, signal deadlocks, process RSS visibility in `/ops/status`, and a scheduled Render runtime monitor workflow.
- [ ] Next: deploy the new backend checkpoint to Render and verify whether overlap alerts stay quiet, deadlock frequency drops, worker restarts stop, and reels freshness improves under live load.

## Test / Automation Gap Analysis

Current gaps:

- Render-runtime guard now exists in repo, but it is not live until the required GitHub secrets are configured.
- No always-on post-deploy smoke gate tied to freshness thresholds.
- No automated deadlock-frequency detection for signal ingestion.
- No production telemetry in this workspace to prove mobile shipped-build refresh timing.

Added artifact:

- `src/backend/scripts/operational_check.py`
  - hits `/health`
  - creates an anonymous session
  - probes Articles, Videos, Reels, and session playlist endpoints
  - reports counts, timestamps, cache headers, feed-version stability, and anomalies

## Bottom Line

What is working:

- production API is up
- DB and Redis are healthy
- scheduler leadership is visible
- ingestion is active
- Articles and Videos are fresh and operational
- direct feed cache freshness headers are now trustworthy

What is broken:

- worker runtime is unstable due to repeated OOM kills
- reels freshness is below the backend's own health threshold
- worker logs show deadlocks and overlapping ingestion windows

What is stale:

- reels recent refresh volume, not the entire feed surface

What is not being triggered as expected:

- mobile resume and tab-entry refresh fixes are in repo, but neither is proven in a shipped app build yet
