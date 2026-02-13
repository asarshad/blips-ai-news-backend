# Launch Hardening — Blips Production Readiness

## Executive Summary

**Current Readiness**: Late Alpha / Early Beta  
**Target**: Launch-Ready  
**Last Updated**: 2026-02-13  

The Blips application (backend + mobile + site) requires critical hardening before public release. This document tracks all identified risks, their resolution status, and verification evidence.

---

## Risk Matrix

| ID | Risk | Severity | Component | Status | Fix Strategy |
|----|------|----------|-----------|--------|--------------|
| C1 | Android INTERNET permission missing from release manifest | Critical | Mobile | Not Started | Add `<uses-permission>` to main AndroidManifest.xml |
| C2 | youtube_explode_dart ToS violation + dead video system | Critical | Mobile | Not Started | Remove dormant player system, remove youtube_explode_dart dependency |
| C3 | Health check is no-op (no DB/Redis validation) | Critical | Backend | Not Started | Add real DB+Redis connectivity checks to `/health` |
| C4 | Redis creates new connection per call (no pooling) | Critical | Backend | Not Started | Singleton connection pool in dependencies.py |
| C5 | LLM has no cost controls, no retry, no timeout | Critical | Backend | Not Started | Add tenacity retry, request timeout, daily cost ceiling config |
| C6 | No crash reporting on mobile (TODO stub) | Critical | Mobile | Not Started | Integrate Sentry Flutter SDK |
| H1 | DB connection pool has no tuning or pre_ping | High | Backend | Not Started | Add pool_size, pool_pre_ping, pool_recycle to engine |
| H2 | Dead dependencies and unused code (loguru, lru_cache, _cache) | High | Backend | Not Started | Remove loguru, clean feature_flags dead code |
| H3 | Worker lock refresh has no ownership check (race condition) | High | Backend | Not Started | Use Lua CAS script like main.py scheduler lock |
| H4 | Feature flag cache declared but never used (every check hits Redis) | High | Backend | Not Started | Implement TTL cache or remove dead cache code |
| H5 | No offline/connectivity handling on mobile | High | Mobile | Not Started | Add connectivity_plus, show offline banner, graceful degradation |
| H6 | Gunicorn has no timeout, no max-requests recycling | High | Backend | Not Started | Add --timeout 120 --max-requests 1000 --max-requests-jitter 50 |
| M1 | /metrics endpoint has no authentication | Medium | Backend | Not Started | Add admin API key check |
| M2 | 100+ bare except Exception blocks across backend | Medium | Backend | Not Started | Replace with specific exceptions in touched files |
| M3 | Config anti-pattern: os.getenv() instead of Pydantic native | Medium | Backend | Not Started | Refactor to use Pydantic field defaults |
| M4 | No error boundary on mobile PageView | Medium | Mobile | Not Started | Wrap tabs in try-catch widget |
| M5 | iOS landscape orientation enabled (unintentional) | Medium | Mobile | Not Started | Remove landscape orientations from Info.plist |
| M6 | Version still 1.0.0+1 | Medium | Mobile | Not Started | Bump version for store submission |
| L1 | declarative_base() deprecated in SQLAlchemy 2.0 | Low | Backend | Not Started | Migrate to DeclarativeBase class |
| L2 | openai.api_key global is deprecated pattern | Low | Backend | Not Started | Use OpenAI(api_key=...) client instance |
| L3 | Duplicate routes (tabs + standalone) | Low | Mobile | Not Started | Document or remove standalone routes |

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

1. [ ] All Critical risks verified
2. [ ] All High risks verified
3. [ ] Backend CI passes (lint + unit tests + integration tests)
4. [ ] Mobile `flutter analyze` clean
5. [ ] Mobile `flutter test` passes
6. [ ] `/health` returns unhealthy when DB or Redis is down
7. [ ] LLM failures do not crash the app
8. [ ] Release APK can make network requests
9. [ ] Production crashes are reported to Sentry
10. [ ] No youtube_explode_dart code shipped
11. [ ] Gunicorn workers recycle and have timeouts

---

## Progress Log

| Timestamp | Risk | Change | Verification |
|-----------|------|--------|--------------|
| 2026-02-13 | — | Created LAUNCH_HARDENING.md | — |

