# Production Cleanup Report — Phase 1

> Generated: 2026-03-02 — evidence-based, no deletions in this phase.

---

## Test Baseline (pre-cleanup)

| Suite | Pass | Fail | Skip |
|-------|------|------|------|
| unit + contract | 425 | 0 | 27 |

**Pre-existing issues found during analysis:**
- `tests/contract/test_openapi_snapshot.py` stale (promote/demote routes added in
  `ab300d0` were not reflected). Fixed in this commit by running
  `pytest --update-openapi-snapshot`.

---

## A · Dead / Legacy Python Code

### A-1 · Deprecated Service Shim: `app/services/ingestion_pipeline.py`

**Evidence:**
- Module docstring explicitly states `DEPRECATED`.
- File body: pure re-exports from `app.ingestion` with no logic.
- **Only 1 caller:**
  ```
  src/backend/app/scheduler/tasks_backfill.py:19
      from app.services.ingestion_pipeline import create_ingestion_pipeline
  ```
- `git grep 'ingestion_pipeline'` — no other references.

**Action:** Delete file; update `tasks_backfill.py:19` to import directly from `app.ingestion`.

---

### A-2 · Deprecated Service Shim: `app/services/scoring_service.py`

**Evidence:**
- Module docstring explicitly states `DEPRECATED`.
- File body: pure re-exports from `app.ranking`.
- **2 callers:**
  ```
  app/scheduler/tasks_curation.py:22   from app.services.scoring_service import ScoringService
  tests/test_curation.py:29             from app.services.scoring_service import (…)
  ```
- `git grep 'scoring_service'` — no other production references.

**Action:** Delete file; update both callers to import from `app.ranking`.

---

### A-3 · Deprecated Service Shim: `app/services/clustering_service.py`

**Evidence:**
- Module docstring explicitly states `DEPRECATED`.
- File body: pure re-exports from `app.clustering`.
- **2 callers:**
  ```
  app/scheduler/tasks_curation.py:52   from app.services.clustering_service import ClusteringService
  tests/test_curation.py:22            from app.services.clustering_service import (…)
  ```
- `git grep 'clustering_service'` — no other production references.

**Action:** Delete file; update both callers to import from `app.clustering`.

---

### A-4 · Unused Local Variables (vulture, 100% confidence)

| File | Line | Variable | Context |
|------|------|----------|---------|
| `app/integrations/rss_client.py` | 233 | `article_url` | Assigned but never read after assignment |
| `app/ranking/trend.py` | 55 | `time_window_hours` | Assigned but never read |
| `app/worker.py` | 72 | `frame` | Signal handler — `frame` arg declared but unused |

**Action:** Delete or prefix with `_` (for intentionally unused signal handler arg).

---

### A-5 · Stale OpenAPI Contract Snapshot

**Evidence:** `tests/contract/openapi_snapshot.json` was behind the admin UI changes
(promote/demote routes added 2026-03-02 commit `ab300d0`). Fixed in this phase.

---

## B · Dead / Legacy Dart / Flutter Code

### B-1 · Deprecated Re-export Shim: `optimized_reels_page.dart`

**Evidence:**
```
lib/features/feed/presentation/optimized_reels_page.dart
```
File header:
```dart
/// Re-exports from the reels module for backward compatibility.
///
/// This file is deprecated - import from 'reels/reels.dart' instead.
library;

export 'reels/reels.dart';
```
**Only 1 consumer:**
```
lib/features/feed/presentation/feed_shell_page.dart:11
    import 'package:blips_mobile/features/feed/presentation/optimized_reels_page.dart';
```

**Action:** Update `feed_shell_page.dart` import to `reels/reels.dart` directly; delete shim.

---

### B-2 · Trivial Dart Warnings (`dart analyze`)

| File | Line | Issue |
|------|------|-------|
| `routes/app_router.dart` | 2 | Unused import: `package:flutter/material.dart` |
| `features/chat/presentation/chat_detail_page.dart` | 68 | Unused catch stack variable `stack` |
| `features/feed/presentation/reels/reel_item.dart` | 72 | Always-true null comparison — condition always `true` |
| `features/feed/presentation/widgets/share_service.dart` | 208 | Inferred type argument on `Future.delayed` |

**Action:** One-liner fixes per file.

---

## C · Dependency Audit

### C-1 · Python `requirements.txt` — All Active

| Package | Usage | Status |
|---------|-------|--------|
| `fastapi`, `uvicorn`, `gunicorn` | Core web server | ✅ Active |
| `sqlalchemy`, `psycopg2-binary`, `alembic` | Database | ✅ Active |
| `redis` | Cache + feature flags + scheduler lock | ✅ Active |
| `apscheduler` | Background jobs | ✅ Active |
| `openai` | LLM provider (primary) | ✅ Active |
| `mistralai` | LLM provider (fallback) — `llm_client.py:198` | ✅ Active |
| `feedparser`, `beautifulsoup4`, `lxml`, `requests` | RSS parsing | ✅ Active |
| `httpx`, `trafilatura`, `readability-lxml` | Content extraction | ✅ Active |
| `langdetect` | Language filter in `ingestion/language_filter.py` | ✅ Active |
| `python-dotenv`, `pydantic`, `pydantic-settings` | Config/validation | ✅ Active |
| `slowapi` | Rate limiting | ✅ Active |
| `tenacity` | Retry logic in LLM client | ✅ Active |
| `python-dateutil` | Date parsing in ingestion | ✅ Active |
| `python-multipart` | Form handling (admin UI) | ✅ Active |

**Conclusion:** No Python production dependencies can be removed.

---

### C-2 · Flutter `pubspec.yaml` — 2 Unused Dependencies Found

#### `screenshot: ^3.0.0` — ❌ UNUSED

**Evidence:**
```
$ grep -rn "import 'package:screenshot" lib/ --include="*.dart"
(no matches)
```
The package never appears in any `import` statement. The word "screenshot" appears only
in comments in `debug_overlay.dart` and `share_service.dart` (the latter uses
`share_plus`, not `screenshot`).

**Action:** Remove from `pubspec.yaml`; run `flutter pub get`.

#### `flutter_query: ^0.3.7` — ❌ UNUSED

**Evidence:**
```
$ grep -rn "import 'package:flutter_query" lib/ --include="*.dart"
(no matches)
```
No file in `lib/` imports this package. Data fetching is done via Riverpod providers
and `Dio` directly.

**Action:** Remove from `pubspec.yaml`; run `flutter pub get`.

---

### C-3 · Flutter `dev_dependencies` — All Active

| Package | Usage |
|---------|-------|
| `flutter_test` | Test framework |
| `integration_test` | Integration tests |
| `network_image_mock` | Mock network images in widget tests |
| `very_good_analysis` | lint rules via `analysis_options.yaml` |
| `flutter_launcher_icons` | Used in `pubspec.yaml` flutter_launcher_icons section |
| `flutter_native_splash` | Used in `pubspec.yaml` flutter_native_splash section |
| `analyzer` | Static analysis |

---

## D · Documentation Audit

### D-1 · Overlapping / Duplicate Doc Files

28 markdown files across 3 locations (see full list in `CLEANUP_PLAN.md §3`).

**Confirmed overlaps (checked by inspection):**
- `src/backend/ARCHITECTURE.md` ↔ `docs/ARCHITECTURE.md` — 75% content overlap
- `src/backend/CONFIGURATION.md` ↔ `docs/CONFIGURATION.md` + `docs/ENV_REFERENCE.md`
- `src/backend/DEVELOPMENT.md` ↔ `docs/DEVELOPMENT_GUIDE.md`
- `src/backend/DEPLOYMENT.md` ↔ `docs/OPERATIONS_RUNBOOK.md`

**Confirmed stale / historical (safe to delete):**
- `src/backend/PHASE7_DEPLOYMENT.md` — one-time Render setup log, Phase 7 is done
- `HARDENING_PROGRESS.md` — Launch hardening tracker, all checkboxes done
- `MIGRATION_TO_CURATION_ONLY.md` — migration completed (alembic migration `4b89bee76a66`)
- `TASKS.md` — ad-hoc task tracker, superseded by GitHub Issues
- `deployment-guide.md` — duplicates content in `render.yaml` + `docs/OPERATIONS_RUNBOOK.md`

---

## E · Confirmed NOT Dead (do not touch)

| Item | Reason |
|------|--------|
| `app/worker.py` | Production worker entry point: `render.yaml:198 startCommand: python -m app.worker` |
| `app/integrations/fake_llm.py` | Used by `llm_client.py:289` when `ENABLE_FAKE_LLM=true` |
| `app/core/feature_flags.py` | Used widely; `is_enabled()` called in scheduler tasks + routes |
| `core/database/database_helper.dart` | Used for local SQLite caching of reels data |
| `mistralai` dep | `MistralLLMClient` in `llm_client.py:198` |

---

## F · Execution Plan for Phase 2

Order chosen to minimize regression risk (callers updated _before_ files deleted):

| Step | Task | Risk |
|------|------|------|
| F-1 | Fix unused variables (Python) | Zero |
| F-2 | Remove `app/services/ingestion_pipeline.py` + update `tasks_backfill.py` | Low |
| F-3 | Remove `app/services/scoring_service.py` + update `tasks_curation.py` + `test_curation.py` | Low |
| F-4 | Remove `app/services/clustering_service.py` + update `tasks_curation.py` + `test_curation.py` | Low |
| F-5 | Remove Flutter re-export shim + update `feed_shell_page.dart` | Low |
| F-6 | Fix trivial Dart warnings | Zero |
| F-7 | Remove `screenshot` + `flutter_query` from `pubspec.yaml` | Zero |
