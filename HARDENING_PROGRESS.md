# 30-Day Hardening Progress Tracker

**Start Date**: February 13, 2026  
**Target Completion**: March 15, 2026  
**Current Status**: Week 1 Complete - Starting Week 2

---

## Week 1: Stability & Monitoring (Days 1-7)

### Task 1.1: Slack/Webhook Alerting for Health Failures
- [x] Create `app/services/alerting_service.py`
- [x] Add `ALERT_WEBHOOK_URL` and `ALERT_ENABLED` to config
- [x] Integrate alerting into health check (503 triggers alert)
- [x] Add to `render.yaml` env vars
- [ ] Verification: Health check failure triggers Slack message

**Status**: ✅ Complete (code done, needs runtime verification)

### Task 1.2: Ingestion Stall Detection
- [x] Add `check_ingestion_health` scheduled job (tasks_health.py)
- [x] Alert if no inserts in past 2 hours
- [x] Add metrics: `last_successful_ingestion_at`, `articles_ingested_last_2h` to /metrics
- [ ] Verification: Stop ingestion → alert fires within 2.5h

**Status**: ✅ Complete (code done, needs runtime verification)

### Task 1.3: Per-Source Ingestion Health Metrics
- [x] Create `/metrics/sources` endpoint (uses existing tables)
- [x] Shows per-source success/failure rates from ingestion_progress
- [x] Shows problem feeds (failed status or high retry count)
- [x] No migration needed - leverages existing SourceDailyStat and IngestionProgress
- [ ] Verification: Endpoint shows success rates for all feeds

**Status**: ✅ Complete (code done, needs runtime verification)

### Task 1.4: Cold Start Prevention (Keepalive)
- [x] Create `.github/scripts/keep_alive_ping.py` script
- [x] Add `.github/workflows/keepalive.yml` (every 10 min)
- [x] Script supports single-ping and multi-ping modes
- [ ] Add `RENDER_HEALTH_URL` secret to GitHub repo
- [ ] Verification: First request <5s after 12h inactivity

**Status**: ✅ Complete (code done, needs secret config)

### Task 1.5-1.7: Critical Path Test Coverage
- [x] Tiered feed service tests (existing - 361 lines comprehensive tests)
- [x] Chat quota enforcement tests (created test_chat_quota.py - 10 tests)
- [x] Ingestion budget tests (created test_ingestion_budget.py - 8 tests)
- [ ] Verification: >60% coverage on critical services

**Status**: ✅ Complete (tests created, run in CI)

---

## Week 1 Summary

**Completed Items:**
1. ✅ Webhook alerting service with rate limiting
2. ✅ Ingestion stall detection (30-min job)
3. ✅ Per-source health metrics endpoint
4. ✅ Keepalive GitHub Action workflow
5. ✅ Critical path unit tests

**Files Created:**
- `src/backend/app/services/alerting_service.py`
- `src/backend/app/scheduler/tasks_health.py`
- `src/backend/app/api/routes/metrics.py`
- `.github/scripts/keep_alive_ping.py`
- `.github/workflows/keepalive.yml`
- `src/backend/tests/unit/test_chat_quota.py`
- `src/backend/tests/unit/ingestion/test_ingestion_budget.py`

**Files Modified:**
- `src/backend/app/core/config.py` - Added alerting config
- `src/backend/app/main.py` - Integrated alerting, metrics
- `src/backend/app/scheduler/__init__.py` - Added health check job
- `src/backend/app/scheduler/tasks.py` - Exported health functions
- `src/backend/app/api/__init__.py` - Added metrics router
- `render.yaml` - Added alerting env vars

**Next:** Week 2 - Mobile Polish

---

## Week 2: Mobile Polish (Days 8-14)

### Task 2.1-2.2: Memory Profiling
- [x] Create MEMORY_PROFILING.md documentation
- [x] Create `lib/core/config/memory_config.dart`
- [x] Make player pool size configurable via MemoryConfig
- [ ] Add memory logging (optional debug feature)
- [ ] Verification: <300MB after 100 reel scrolls

**Status**: 🟡 In Progress

### Task 2.3-2.4: Scroll Performance
- [ ] Profile frame drops
- [ ] Optimize lazy image loading
- [ ] Verification: <5% frames >16ms

**Status**: ⚪ Not Started

### Task 2.5-2.7: Mobile Test Coverage
- [x] Article card widget tests (10 tests)
- [x] Reel item widget tests (7 tests)
- [x] Floating chat bubbles test (existing, 10 tests)
- [ ] Expand integration smoke test
- [ ] Verification: All tests pass ✅ (27 widget tests passing)

**Status**: 🟡 In Progress

**Files Created:**
- `test/widget/article_card_test.dart`
- `test/widget/reel_item_test.dart`

---

## Week 3: Resilience (Days 15-21)

### Task 3.1: Circuit Breaker for YouTube API
- [x] Create `app/core/circuit_breaker.py`
- [x] Integrate into YouTube client (3 HTTP call sites wrapped)
- [x] 13 unit tests in `tests/unit/test_circuit_breaker.py`
- [x] Verification: 5 failures → circuit opens → recovers after timeout

**Files Created**: `app/core/circuit_breaker.py`, `tests/unit/test_circuit_breaker.py`
**Files Modified**: `app/integrations/youtube_client.py`
**Status**: ✅ Complete

### Task 3.2-3.3: User-Friendly Error Messages
- [x] Create `app/core/error_codes.py` with 15 stable error codes
- [x] Update `main.py` global exception handler with structured `code`/`message` fields
- [x] Update mobile `app_exception.dart` to parse backend error codes
- [x] Custom rate limit handler with error codes
- [x] Verification: All API errors return `{detail, code, message}` structure

**Files Created**: `app/core/error_codes.py`
**Files Modified**: `app/main.py`, `blips-mobile/lib/core/error/app_exception.dart`
**Status**: ✅ Complete

### Task 3.4: Redis Fail-Closed Rate Limiting
- [x] Added `redis_health_guard` middleware to `main.py`
- [x] Cached health check every 5s to avoid per-request overhead
- [x] Returns 503 with `service_unavailable` error code when Redis down
- [x] Skips health/metrics/docs paths
- [x] Verification: No Redis → 503 response with structured error

**Files Modified**: `app/main.py`
**Status**: ✅ Complete

### Task 3.5-3.6: Database Failover
- [x] Document failover procedure in `OPERATIONS_RUNBOOK.md`
- [x] Verify pool_pre_ping, pool_recycle, pool_size configured
- [x] 6 integration tests in `tests/integration/test_db_reconnection.py`
- [x] Verification: Pool configuration validated, recovery tested

**Files Created**: `tests/integration/test_db_reconnection.py`
**Files Modified**: `docs/OPERATIONS_RUNBOOK.md`
**Status**: ✅ Complete

---

## Week 4: Release Prep (Days 22-30)

### Task 4.1-4.2: iOS App Store Submission
- [x] Version bumped in pubspec.yaml (1.0.2+3) — iOS reads from Flutter
- [x] App Store description/keywords prepared (`store_metadata/app_store/description.md`)
- [x] Pre-launch checklist created (`PRE_LAUNCH_CHECKLIST.md`)
- [ ] Create App Store Connect listing (manual)
- [ ] Upload screenshots (see `store_metadata/screenshots/README.md`)
- [ ] Build and submit IPA: `flutter build ipa --release`
- [ ] Verification: Submitted for review

**Status**: 🟡 Prep Done — Manual submission remaining

### Task 4.3-4.4: Android Play Store Submission
- [x] Version bumped in pubspec.yaml (1.0.2+3) — Android reads from Flutter
- [x] Play Store description prepared (`store_metadata/play_store/listing.md`)
- [x] Release signing guide created (`android/app/signing.md`)
- [ ] Generate upload keystore and configure `key.properties` (manual — see signing.md)
- [ ] Create Play Console listing (manual)
- [ ] Upload screenshots (see `store_metadata/screenshots/README.md`)
- [ ] Build and submit AAB: `flutter build appbundle --release`
- [ ] Verification: Submitted for review

**Status**: 🟡 Prep Done — Signing setup & manual submission remaining

### Task 4.5-4.6: Soft Launch
- [x] Pre-launch checklist includes soft launch criteria
- [ ] TestFlight to 100 users (manual)
- [ ] Internal testing track on Android (manual)
- [ ] Create feedback form (manual)
- [ ] Monitor crash-free rate
- [ ] Verification: >99% crash-free

**Status**: 🟡 Prep Done — Manual execution remaining

### Task 4.7-4.8: Incident Runbooks & Stability
- [x] Added 5 incident runbooks to OPERATIONS_RUNBOOK.md (INC-1 through INC-5)
- [ ] 7-day stability checkpoint (run after soft launch)
- [ ] Verification: All criteria met

**Status**: 🟢 Runbooks Complete — Stability checkpoint pending

---

## Final Checklist (Day 30)

- [ ] `/health` failures trigger Slack alert
- [ ] Ingestion stall >2h triggers alert
- [ ] Per-source metrics available at `/metrics/sources`
- [ ] Cold start <5s verified
- [ ] 60%+ unit test coverage on critical paths
- [ ] Memory <300MB on low-RAM device
- [ ] Circuit breaker protects YouTube API
- [ ] Rate limiting fails closed
- [ ] iOS submitted to App Store
- [ ] Android submitted to Play Console
- [ ] 100 beta users onboarded
- [ ] Incident runbooks documented

---

## Change Log

| Date | Task | Status | Notes |
|------|------|--------|-------|
| Day 22 | 4.1-4.2 iOS Prep | 🟡 Prep Done | Version 1.0.2+3, App Store listing written |
| Day 22 | 4.3-4.4 Android Prep | 🟡 Prep Done | Play Store listing written, signing guide created |
| Day 22 | 4.7-4.8 Incident Runbooks | 🟢 Complete | INC-1 through INC-5 added to OPERATIONS_RUNBOOK.md |
| Day 22 | 4.5-4.6 Soft Launch Prep | 🟡 Prep Done | Pre-launch checklist created |
| 2026-02-13 | Task 1.1 | Started | Creating alerting service |

