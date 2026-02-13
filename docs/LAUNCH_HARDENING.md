# Launch Hardening — Blips Production Readiness

## Executive Summary

**Current Readiness**: Beta — All Critical and High risks resolved  
**Target**: Launch-Ready  
**Last Updated**: 2025-07-25  

The Blips application (backend + mobile + site) requires critical hardening before public release. This document tracks all identified risks, their resolution status, and verification evidence.

---

## Risk Matrix

| ID | Risk | Severity | Component | Status | Fix Strategy |
|----|------|----------|-----------|--------|--------------|
| C1 | Android INTERNET permission missing from release manifest | Critical | Mobile | **Verified** | Add `<uses-permission>` to main AndroidManifest.xml |
| C2 | youtube_explode_dart ToS violation + dead video system | Critical | Mobile | **Verified** | Remove dormant player system, remove youtube_explode_dart dependency |
| C3 | Health check is no-op (no DB/Redis validation) | Critical | Backend | **Verified** | Add real DB+Redis connectivity checks to `/health` |
| C4 | Redis creates new connection per call (no pooling) | Critical | Backend | **Verified** | Singleton connection pool in dependencies.py |
| C5 | LLM has no cost controls, no retry, no timeout | Critical | Backend | **Verified** | Add tenacity retry, request timeout, daily cost ceiling config |
| C6 | No crash reporting on mobile (TODO stub) | Critical | Mobile | **Verified** | Integrate Sentry Flutter SDK |
| H1 | DB connection pool has no tuning or pre_ping | High | Backend | **Verified** | Add pool_size, pool_pre_ping, pool_recycle to engine |
| H2 | Dead dependencies and unused code (loguru, lru_cache, _cache) | High | Backend | **Verified** | Remove loguru, clean feature_flags dead code |
| H3 | Worker lock refresh has no ownership check (race condition) | High | Backend | **Verified** | Use Lua CAS script like main.py scheduler lock |
| H4 | Feature flag cache declared but never used (every check hits Redis) | High | Backend | **Verified** | Implement TTL cache or remove dead cache code |
| H5 | No offline/connectivity handling on mobile | High | Mobile | **Verified** | Add connectivity_plus, show offline banner, graceful degradation |
| H6 | Gunicorn has no timeout, no max-requests recycling | High | Backend | **Verified** | Add --timeout 120 --max-requests 1000 --max-requests-jitter 50 |
| M1 | /metrics endpoint has no authentication | Medium | Backend | **Verified** | Add admin API key check |
| M2 | 100+ bare except Exception blocks across backend | Medium | Backend | **Verified** | Replace with specific exceptions in touched files |
| M3 | Config anti-pattern: os.getenv() instead of Pydantic native | Medium | Backend | **Verified** | Refactor to use Pydantic field defaults |
| M4 | No error boundary on mobile PageView | Medium | Mobile | **Verified** | Wrap tabs in try-catch widget |
| M5 | iOS landscape orientation enabled (unintentional) | Medium | Mobile | **Verified** | Remove landscape orientations from Info.plist |
| M6 | Version still 1.0.0+1 | Medium | Mobile | **Verified** | Bump version for store submission |
| L1 | declarative_base() deprecated in SQLAlchemy 2.0 | Low | Backend | **Verified** | Migrate to DeclarativeBase class |
| L2 | openai.api_key global is deprecated pattern | Low | Backend | **Verified** | Use OpenAI(api_key=...) client instance |
| L3 | Duplicate routes (tabs + standalone) | Low | Mobile | **Verified** | Document or remove standalone routes |

---

## Severity Grouping

### Critical (Must fix before any release)
- **C1**: Android INTERNET permission — release APK has zero network connectivity
- **C2**: youtube_explode_dart — YouTube ToS violation, dead code shipped to users
- **C3**: Health check — Render cannot detect if dependencies are down
- **C4**: Redis pooling — connection exhaustion under load
- **C5**: LLM controls — unbounded cost, no resilience to provider outages
- **C6**: Crash reporting — production crashes are invisible

### High (Must fix before public release)
- **H1**: DB pool — stale connections cause 500 errors after DB restarts
- **H2**: Dead code — unnecessary attack surface and confusion
- **H3**: Worker lock — two workers could race on ingestion
- **H4**: Feature flags — unnecessary Redis load on every request
- **H5**: Offline handling — app shows blank screen when offline
- **H6**: Gunicorn — workers hang on slow LLM calls, memory leaks

### Medium (Should fix before public release)
- **M1–M6**: See risk matrix above

### Low (Nice to have)
- **L1–L3**: See risk matrix above

---

## Launch Gate Criteria

Before public release, ALL of the following must be true:

1. [x] All Critical risks verified
2. [x] All High risks verified
3. [x] Backend CI passes (lint + unit tests + integration tests)
4. [x] Mobile `flutter analyze` clean
5. [x] Mobile `flutter test` passes
6. [x] `/health` returns unhealthy when DB or Redis is down
7. [x] LLM failures do not crash the app
8. [x] Release APK can make network requests
9. [x] Production crashes are reported to Sentry
10. [x] No youtube_explode_dart code shipped
11. [x] Gunicorn workers recycle and have timeouts

---

## Progress Log

| Timestamp | Risk | Change | Verification |
|-----------|------|--------|--------------|
| 2025-07-25 | — | Created LAUNCH_HARDENING.md | — |
| 2025-07-25 | C1 | Added INTERNET permission to AndroidManifest.xml | Commit `261d9de` on blips-mobile |
| 2025-07-25 | C2 | Removed youtube_explode_dart + 11 dead files (1802 lines) | Commit `7993b90` on blips-mobile, 18 files changed |
| 2025-07-25 | C3 | Health check validates DB (SELECT 1) + Redis (ping), returns 503 on failure | Commit `749460b`, 126 tests pass |
| 2025-07-25 | C4 | Redis singleton ConnectionPool (max_connections=20, socket_timeout=5) | Commit `4e70154`, 126 tests pass |
| 2025-07-25 | C5 | Tenacity retry (3x exp backoff), 30s timeout, $5 daily cost ceiling via Redis | Commit `19dbf1f`, 126 tests pass |
| 2025-07-25 | C6 | Sentry Flutter SDK integrated, error_handler reports to Sentry in release | Commit `2ebf233` on blips-mobile, 26 tests pass |
| 2025-07-25 | H1 | Engine pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=1800 | Commit `82a6480`, 126 tests pass |
| 2025-07-25 | H2+H4 | Removed loguru, TTL cache for feature flags (10s), lazy proxy | Commit `01221de`, 126 tests pass |
| 2025-07-25 | H3 | Worker lock uses Lua CAS script for ownership verification | Commit `eb8b1aa`, 126 tests pass |
| 2025-07-25 | H5 | connectivity_plus + OfflineBanner in FeedShellPage | Commit `980fd2d` on blips-mobile, 26 tests pass |
| 2025-07-25 | H6 | Gunicorn: --timeout 120, --graceful-timeout 30, --max-requests 1000, --access-logfile - | Commit `d29705f`, 126 tests pass |
| 2026-02-13 | M1 | Added Depends(require_admin_key) to /metrics endpoint | Commit `0744d38`, 126 tests pass |
| 2026-02-13 | M3 | Replaced 60+ os.getenv() calls with Pydantic native env resolution | Commit `9d7aa07`, 126 tests pass |
| 2026-02-13 | M5+M6 | Locked iOS to portrait-only, bumped version to 1.0.1+2 | Commit `9a2d930` on blips-mobile, 26 tests pass |
| 2026-02-13 | M4 | Created ErrorBoundary widget, wrapped PageView tabs | Commit `7bbf2f3` on blips-mobile, 26 tests pass |
| 2026-02-13 | L3 | Removed duplicate /chat and /settings GoRoutes | Commit `bd69a08` on blips-mobile, 26 tests pass |
| 2026-02-13 | M2 | Replaced 31 bare except blocks with specific types in 6 core files | Commit `d3ac163`, 126 tests pass |

