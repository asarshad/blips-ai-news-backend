# Production Cleanup Plan

> Generated: 2026-03-02 — Principal Engineer audit

---

## Definition of Done

- [ ] All deprecated shim modules removed; callers updated to direct imports
- [ ] All unused local variables eliminated (vulture clean)
- [ ] Deprecated Flutter re-export shim removed; consumer updated
- [ ] `src/backend/*.md` docs merged into `docs/` or deleted
- [ ] Root-level transient docs (TASKS, HARDENING_PROGRESS, MIGRATION_TO_CURATION_ONLY, deployment-guide) deleted
- [ ] `docs/` contains exactly the canonical doc set (no duplicates, no contradictions)
- [ ] `make verify` target runs: format → lint → unit tests → integration tests
- [ ] CI uses the same commands as `make verify`
- [ ] Full test suite passes locally after all changes
- [ ] `git grep` confirms no references to removed symbols
- [ ] `dart analyze lib/` reports 0 errors, 0 warnings

---

## 1. Current Architecture Overview (Actual)

### Backend — `blips-ai-news-backend`

Single FastAPI service running on Render, split into **two processes** in production:

| Process | Entry Point | Responsibility |
|---------|-------------|----------------|
| Web API | `app.main:app` via gunicorn | HTTP endpoints, health, admin UI |
| Worker | `python -m app.worker` | Scheduled ingestion, scoring, promotion |

In development both run in a single process (scheduler embedded in web).

**Key subsystems:**

```
src/backend/app/
├── api/          # FastAPI route handlers + admin HTML UI
│   ├── routes/   # articles, videos, session, chat, admin, debug, metrics …
│   └── admin/    # admin CRUD + server-rendered Tailwind UI
├── clustering/   # Dedup key + similarity + ClusteringService
├── config/       # Typed Pydantic Settings + named config objects
├── core/         # Auth, feature flags, logging, circuit breaker, exceptions
├── db/           # SQLAlchemy SessionLocal + Base
├── domain/       # Domain services (editorial workflow)
├── extraction/   # Web page fetching, text extraction (trafilatura+readability)
├── ingestion/    # IngestionPipeline, signal ingestion, language filter, leases
│   └── signals/  # HackerNews, GitHub Trending, YouTube Trending crawlers
├── integrations/ # RSS client, YouTube client, LLM client (OpenAI/Mistral/Fake)
├── models/       # SQLAlchemy ORM models
├── quality/      # Quality report builder
├── ranking/      # Scoring: quality, recency, trend, diversity, global_score
├── repositories/ # Data access layer (one repo per model group)
├── scheduler/    # APScheduler jobs: ingestion, scoring, promotion, cleanup …
└── services/     # Higher-level services (playlist, personalization, feed, …)
                  # ⚠️  Also contains 3 DEPRECATED shim re-export files
```

**Infrastructure:** PostgreSQL (SQLAlchemy/Alembic), Redis (cache + feature flags + scheduler lock), Render (hosting).

### Mobile — `blips-mobile`

Flutter/Dart app (iOS primary). Architecture: Riverpod + flutter_hooks, feature-folder structure.

```
lib/
├── core/         # Network (Dio), theme, error handling, local DB, device ID
├── features/
│   ├── ads/      # Ad card, banner slot, config + event service
│   ├── chat/     # AI chat feature
│   ├── feed/     # Articles, videos, reels; feed caching; YouTube player
│   └── settings/ # Theme toggle, settings page
└── routes/       # go_router configuration
```

---

## 2. Suspected Dead / Legacy Areas

### Python (backend)

| # | Path | Evidence | Risk |
|---|------|----------|------|
| 1 | `app/services/ingestion_pipeline.py` | Module docstring: **DEPRECATED**. Pure re-export shim for `app.ingestion`. One caller: `scheduler/tasks_backfill.py:19` (lazy import). | Low — remove shim, update import |
| 2 | `app/services/scoring_service.py` | Module docstring: **DEPRECATED**. Pure re-export shim for `app.ranking`. Callers: `scheduler/tasks_curation.py:22`, `tests/test_curation.py:29`. | Low — remove shim, update imports |
| 3 | `app/services/clustering_service.py` | Module docstring: **DEPRECATED**. Pure re-export shim for `app.clustering`. Callers: `scheduler/tasks_curation.py:52`, `tests/test_curation.py:22`. | Low — remove shim, update imports |
| 4 | `app/integrations/rss_client.py:233` | `vulture`: `article_url` assigned but never read (100% confidence) | Trivial |
| 5 | `app/ranking/trend.py:55` | `vulture`: `time_window_hours` assigned but never read (100% confidence) | Trivial |
| 6 | `app/worker.py:72` | `vulture`: `frame` variable unused (signal handler traceback variable) | Trivial |

### Dart / Flutter (mobile)

| # | Path | Evidence | Risk |
|---|------|----------|------|
| 7 | `lib/features/feed/presentation/optimized_reels_page.dart` | Header comment: **deprecated re-export shim**. Single consumer: `feed_shell_page.dart:11`. | Low — update import in consumer, delete shim |
| 8 | `routes/app_router.dart:2` | `dart analyze`: `package:flutter/material.dart` unused import | Trivial |
| 9 | `features/chat/presentation/chat_detail_page.dart:68` | `dart analyze`: unused catch `stack` variable | Trivial |
| 10 | `features/feed/presentation/reels/reel_item.dart:72` | `dart analyze`: always-true null check, unnecessary condition | Trivial |

---

## 3. Documentation Audit

### Current state — 28 markdown files in 3 locations

**`docs/` (project root — 13 files):**
ARCHITECTURE.md, BACKEND_STRUCTURE.md, CONFIGURATION.md, CONTENT_SOURCES.md,
DEVELOPMENT_GUIDE.md, ENV_REFERENCE.md, FEED_FRESHNESS_STRATEGY.md, INGESTION.md,
INVENTORY_RUNBOOK.md, LAUNCH_HARDENING.md, OPERATIONS_RUNBOOK.md,
PRIVACY_DECLARATIONS.md, QA_TEST_PLAN.md

**`src/backend/` (backend root — 7 files):**
ARCHITECTURE.md, CONFIGURATION.md, DEPLOYMENT.md, DEVELOPMENT.md,
PHASE7_DEPLOYMENT.md, README.md, RELEASE_CHECKLIST.md

**Project root (8 files):**
AGENT_GUIDE.md, DECISIONS.md, deployment-guide.md, HARDENING_PROGRESS.md,
MIGRATION_TO_CURATION_ONLY.md, README.md, SECURITY.md, TASKS.md

### Canonical target (Phase 3 goal)

Keep exactly:

| File | Location | Purpose |
|------|----------|---------|
| `README.md` | project root | What it is, quick-start, deploy |
| `docs/ARCHITECTURE.md` | docs/ | Technical architecture + repo map |
| `docs/DEVELOPMENT.md` | docs/ | Local dev, tests, lint, pre-commit |
| `docs/OPERATIONS.md` | docs/ | Env vars, deploy, monitoring, runbooks |
| `docs/CONTENT_SOURCES.md` | docs/ | RSS feeds + YouTube channels (keep) |
| `docs/PRIVACY_DECLARATIONS.md` | docs/ | App Store privacy (keep) |
| `SECURITY.md` | project root | Security contact + policy (keep) |
| `DECISIONS.md` | project root | Architecture decision log (keep) |

### Files to delete / merge

| File | Action |
|------|--------|
| `src/backend/ARCHITECTURE.md` | Delete (content ⊂ `docs/ARCHITECTURE.md`) |
| `src/backend/CONFIGURATION.md` | Delete (merge any new env vars into `docs/OPERATIONS.md`) |
| `src/backend/DEVELOPMENT.md` | Delete (merge into `docs/DEVELOPMENT.md`) |
| `src/backend/DEPLOYMENT.md` | Delete (merge deploy steps into `docs/OPERATIONS.md`) |
| `src/backend/PHASE7_DEPLOYMENT.md` | Delete (historical one-time deployment log) |
| `src/backend/RELEASE_CHECKLIST.md` | Merge into `docs/OPERATIONS.md` |
| `src/backend/README.md` | Delete (redirect to project root README) |
| `docs/BACKEND_STRUCTURE.md` | Delete (merge into `docs/ARCHITECTURE.md`) |
| `docs/CONFIGURATION.md` | Delete (merge into `docs/OPERATIONS.md`) |
| `docs/DEVELOPMENT_GUIDE.md` | Rename → `docs/DEVELOPMENT.md` |
| `docs/ENV_REFERENCE.md` | Delete (merge into `docs/OPERATIONS.md`) |
| `docs/FEED_FRESHNESS_STRATEGY.md` | Delete (extract key points → `docs/ARCHITECTURE.md`) |
| `docs/INGESTION.md` | Delete (merge key content → `docs/ARCHITECTURE.md`) |
| `docs/INVENTORY_RUNBOOK.md` | Merge into `docs/OPERATIONS.md` |
| `docs/LAUNCH_HARDENING.md` | Delete (historical launch checklist, done) |
| `docs/OPERATIONS_RUNBOOK.md` | Rename → `docs/OPERATIONS.md` |
| `docs/QA_TEST_PLAN.md` | Keep or merge into `docs/DEVELOPMENT.md` |
| Root `deployment-guide.md` | Delete (superseded by `docs/OPERATIONS.md`) |
| Root `HARDENING_PROGRESS.md` | Delete (historical progress tracker, done) |
| Root `MIGRATION_TO_CURATION_ONLY.md` | Delete (historical migration, complete) |
| Root `TASKS.md` | Delete (task tracker; use GitHub Issues) |
| Root `AGENT_GUIDE.md` | Keep (instructions for AI agents in this repo) |

---

## 4. Dependency Audit Plan

### Python (`requirements.txt`)

All 19 production dependencies appear active. Audit to confirm:
- `mistralai` — used by `MistralLLMClient` in `llm_client.py`; optional LLM fallback ✓  
- `langdetect` — used by `language_filter.py` ✓
- `trafilatura` + `readability-lxml` — text extraction pipeline ✓
- `slowapi` — rate limiter in `main.py` ✓
- `apscheduler` — scheduler in `scheduler/__init__.py` ✓

### Flutter (`pubspec.yaml`)

Review these deps:
- `screenshot: ^3.0.0` — check if referenced anywhere (sharing feature?)
- `sqflite` + `path` — used by `database_helper.dart` local cache
- `flutter_query` — check adoption vs native Riverpod pattern
- `very_good_analysis` — dev dep, rules `analysis_options.yaml` (keep)

---

## 5. Progress Tracking

Phase status will be updated here as work is done.

- [ ] Phase 0 — Inventory & Plan (this doc)
- [ ] Phase 1 — Dead code report (`docs/CLEANUP_REPORT.md`)
- [ ] Phase 2 — Remove 3 deprecated shim services
- [ ] Phase 2 — Fix unused variables (Python)
- [ ] Phase 2 — Remove Flutter re-export shim
- [ ] Phase 2 — Fix trivial Dart warnings  
- [ ] Phase 3 — Consolidate documentation
- [ ] Phase 4 — Add `make verify` + strengthen CI
