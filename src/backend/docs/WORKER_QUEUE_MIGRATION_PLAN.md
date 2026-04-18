# Worker Queue Migration Plan

## Scope

This document captures the current background-job architecture in `src/backend/app/`,
the concrete bottlenecks that make the article pipeline feel serialized, and a
production-safe migration path toward explicit queue-driven workers.

It is based on the current codepaths as of 2026-04-18.

## Current Facts

### Worker entrypoints and schedulers

- The production worker entrypoint is [`app/worker.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/worker.py).
- Render runs a single dedicated worker service with `python -u -m app.worker` and `SCHEDULER_ENABLED=true`; the API service has `SCHEDULER_ENABLED=false` in [`render.yaml`](/Users/ra/dev/projects/blips/blips-ai-news-backend/render.yaml).
- `app.worker` acquires a Redis leader lock (`SCHEDULER_LEADER_LOCK_KEY`) so only one process owns the scheduler lane at a time.
- APScheduler jobs are registered in [`app/scheduler/__init__.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/__init__.py); every registered recurring job uses `max_instances=1`.

### Current long-running / background jobs

- `fetch_news` in [`tasks_ingestion.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_ingestion.py)
  - runs checkpointed ingestion
  - optionally runs video discovery
  - runs clustering inline
  - runs promotion inline
  - runs immediate AI summarization inline
- `promotion_job` in [`tasks_promotion.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_promotion.py)
- `ai_retry_job` in [`tasks_ai_retry.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_ai_retry.py)
- `article_image_verification_job` in [`tasks_article_image.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_article_image.py)
- `content_event_dispatch_job` in [`tasks_content_events.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_content_events.py)
- `signal_ingestion_job` in [`tasks_signals.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/scheduler/tasks_signals.py)
- `clustering_job`, `scoring_job`, `backfill_job`, health checks, cleanup

### Existing queue-like infrastructure

- Durable outbox table: [`app/models/content_event.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/models/content_event.py)
- Outbox dispatcher with `FOR UPDATE SKIP LOCKED`: [`app/services/content_event_dispatcher.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/services/content_event_dispatcher.py)
- Dedicated outbox-consumer entrypoint for future split workers: [`app/content_event_worker.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/content_event_worker.py)
- Existing outbox event types:
  - `content.ready`
  - `content.unready`
  - `article.image_verification.requested`
  - `content.ai_summary.requested`
  - `content.promotion_eval.requested`
- Ingestion already uses explicit leasing and row ownership:
  - Redis leases in [`app/ingestion/leases.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/ingestion/leases.py)
  - Postgres advisory locks in [`app/ingestion/checkpoint_locks.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/ingestion/checkpoint_locks.py)
  - durable `ingestion_progress` state in [`app/repositories/ingestion_progress_repo.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/repositories/ingestion_progress_repo.py)

### Infrastructure not found

- No Celery, RQ, Dramatiq, Arq, Kafka, SQS, or dedicated multi-process queue worker framework was found in the backend codebase.
- Redis is currently used for leader election, leases, caching, rate limiting, and feature flags, not as a durable job queue.

## Current Article Lifecycle

### RSS / curated ingestion path

1. APScheduler triggers `fetch_and_process_news()`.
2. `run_checkpointed_ingestion()` inserts article rows via PostgreSQL `INSERT .. ON CONFLICT DO NOTHING` in [`app/ingestion/checkpoint_worker.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/ingestion/checkpoint_worker.py).
3. Inserted article stubs are created by [`ArticleHydrationService.build_article_stub()`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/article_hydration.py), which seeds:
   - `article_image_status=PENDING`
   - readiness fields
4. Newly inserted ready rows enqueue `content.ready` via `_queue_ready_events_for_inserted_ids()` in checkpoint ingestion.
5. `fetch_news` then runs:
   - `run_clustering_job(trigger="fetch_news")`
   - `run_promotion_job(trigger="fetch_news")`
   - `run_immediate_ai_summaries(trigger="fetch_news")`
6. AI processing updates summary / topics and calls `sync_content_readiness()`.
7. Once readiness becomes `READY`, the system enqueues `content.ready`.
8. The minute-level outbox dispatcher invalidates feed caches, refreshes playlists, and triggers push.

### Signal ingestion path

1. APScheduler triggers `run_signal_ingestion_job()`.
2. [`run_signal_ingestion()`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/ingestion/signal_ingestion.py):
   - fetches HN / GitHub / YouTube / discovery feeds
   - canonicalizes URLs
   - upserts `signal_urls`
   - inserts thin `CANDIDATE` stubs into `content_items`
3. New signal stubs do not immediately trigger promotion or AI work.
4. They wait for the next scheduled or inline promotion pass.
5. After promotion, they wait for the next scheduled AI retry pass unless they happened to be promoted during `fetch_news`.

### Readiness and image flow

- Readiness logic lives in [`app/services/content_readiness.py`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/services/content_readiness.py).
- For promoted articles:
  - missing / pending image verification produces `awaiting_article_image_verification` or `missing_article_image`
  - missing AI produces `awaiting_ai_processing`
  - ready rows emit `content.ready`
- `sync_content_readiness()` already queues image verification requests via [`queue_article_image_verification_request()`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/services/article_image_service.py).
- Image verification runs through the outbox dispatcher, but dispatch itself is still timer-driven (`content_event_dispatch_job` every minute).

## Where Work Is Serialized In Practice

### 1. Single scheduler lane

- One Render worker owns the APScheduler leader lock.
- Every scheduler job is registered with `max_instances=1`.
- This means background work is effectively serialized at the service level even when different job types are logically independent.

### 2. `fetch_news` is a super-job

`fetch_news` currently bundles ingestion, clustering, promotion, and AI summarization in one synchronous execution path. That makes it the dominant monopoly holder of the worker process.

### 3. Anti-overlap guards intentionally suppress concurrency

- Scheduled clustering skips while `fetch_news` is active and after recent inline clustering.
- Scheduled promotion skips while `fetch_news` is active and after recent inline promotion.
- Scheduled AI retry skips while `fetch_news` is active.
- Scheduled image verification skips while `fetch_news` is active.

These guards are correct for the current single-lane architecture, but they also guarantee that one active job suppresses others.

### 4. Polling-based progression

- promotion: every 30 minutes
- AI retry: every 15 minutes
- image verification sweep: every 5 minutes
- outbox dispatch: every 1 minute
- signals: every 60 minutes

This creates avoidable idle wait between stages even when the next step is already known.

### 5. AI work is still global backlog scanning, not owned jobs

`process_ai_summaries()` builds a worklist by querying promoted backlog rows. It does not claim content rows with row ownership or `SKIP LOCKED`. Multiple AI workers would race and duplicate LLM calls.

### 6. Promotion is not claim-based

`PromotionService._get_candidates()` loads all matching `CANDIDATE` rows without row ownership. Multiple promotion workers would score and promote overlapping sets.

## Current Safeguards and Retry Semantics

### Ingestion

- row ownership through Redis leases and Postgres advisory locks
- additive budgets with `FOR UPDATE` in [`IngestionBudgetRepository`](/Users/ra/dev/projects/blips/blips-ai-news-backend/src/backend/app/repositories/ingestion_budget_repo.py)
- retry/backoff via `retry_at` and `retry_count`
- idempotent inserts via `ON CONFLICT DO NOTHING`

### Outbox dispatch

- event claiming via `FOR UPDATE SKIP LOCKED`
- per-event retry with exponential-ish backoff in `_retry_delay()`
- stale processing lock recovery

### Image verification

- dedupe of pending/processing image-verification events per content item
- revalidation remains idempotent enough to rerun safely

### AI summarization

- per-run LLM call caps
- fixed rate-limit delay between calls
- article retry sentinel for unskimmable articles
- but no per-item claim / ownership token

## Contention / Risk Points Under Parallelization

### Safe today

- outbox claim pattern in `ContentEventDispatcher`
- ingestion row leasing + advisory locking
- upsert-based signal URL dedupe

### Risky if parallelized without new ownership semantics

- promotion candidate scanning
- AI backlog scanning
- article image maintenance sweeps
- direct cache invalidations using Redis `KEYS`
- ready/unready event duplication because `content_event_outbox` has no uniqueness constraint for semantic duplicates

## Proposed Target Architecture

### Worker roles

- `coordinator`
  - lightweight scheduler only
  - source polling, maintenance, safety nets, metrics
- `ingestion-worker`
  - RSS / source ingestion only
- `promotion-worker`
  - `promotion_eval` jobs only
- `article-ai-worker`
  - `article_summary` and `video_summary` jobs only
- `article-image-worker`
  - `article_image_verify` jobs only
- optional `feed-cache-worker`
  - cache invalidation / feed refresh fanout if it becomes heavy

### Queue topics / job types

- `promotion_eval`
  - payload: `content_id`, `content_type`, `reason`, `enqueued_at`, `source`
- `article_summary`
  - payload: `content_id`, `content_type`, `reason`, `enqueued_at`
- `article_image_verify`
  - payload: `content_id`, `source_url`, `readiness_reason`, `enqueued_at`
- `content_ready_fanout`
  - payload: `content_id`, `surfaces`, `effective_type`, `ready_at`
- optional `feed_refresh`
  - payload: `surface`, `content_id`, `cause`

### DB state vs queue state

DB owns:

- durable content lifecycle state
- promotion status
- readiness status / reason
- AI processed flag
- verified image status
- ownership / attempt timestamps if added later

Queue owns:

- what work should happen next
- delivery retries / backoff
- fanout and side effects

### Idempotency strategy

- `promotion_eval`
  - idempotency key: `(content_id, "promotion_eval", content_state_version)`
  - worker must re-read row and no-op if not `CANDIDATE`
- `article_summary`
  - idempotency key: `(content_id, "article_summary")`
  - worker re-reads row and no-ops if already `ai_processed=True`
- `article_image_verify`
  - idempotency key: `(content_id, "article_image_verify")`
  - worker re-reads row and no-ops if already `article_image_status=VERIFIED` and readiness no longer pending image
- `content_ready_fanout`
  - idempotency key: `(content_id, ready_at, event_type)`

### Concurrency controls

- use queue claiming with `FOR UPDATE SKIP LOCKED` or equivalent
- add per-job-type worker concurrency caps
- add explicit LLM concurrency limits for AI workers
- keep one source of truth per state transition in Postgres
- for promotion and AI lanes, move from “scan backlog” to “claim explicit jobs”

## Recommended Migration Path

### Single-Worker Render Constraint

If only one Render worker service is available, we can still create multiple
background execution lanes inside that one process:

- keep `app.worker` as the sole Render worker entrypoint
- keep APScheduler in that process for coordination and safety-net jobs
- start dedicated outbox-consumer threads inside the same process, each scoped
  to specific event types such as promotion, AI summarization, and image/fanout

This preserves the single deployed worker service while letting queue-driven
lanes overlap with the scheduler lane.

### Stage 0: Use the outbox as the first queue substrate

Rationale:

- already deployed
- durable
- already has claim / retry / backoff semantics
- can be consumed by multiple processes later

### Stage 1: Queue AI summary requests when promoted content still needs AI

Why first:

- highest user-visible latency reduction for articles promoted outside `fetch_news`
- reuses existing outbox infrastructure
- does not require removing `ai_retry`
- gives us a concrete pattern for future promotion / image / cache workers

Rollout:

- add new outbox event type behind a feature flag
- queue the event from `sync_content_readiness()` whenever promoted article/video rows still have `ai_processed=False`
- dispatch it through `ContentEventDispatcher`
- leave scheduled `ai_retry` unchanged as fallback

### Stage 2: Queue promotion evaluation for new candidate inserts

- enqueue `promotion_eval` when signal ingestion creates candidate stubs
- later also enqueue from RSS ingestion if promotion is no longer inlined
- keep the existing scheduled promotion job as a catch-up safety net

### Stage 3: Split outbox consumers by job type

- run dedicated consumers for:
  - AI summary
  - image verify
  - ready fanout
- coordinator still enqueues and runs maintenance sweeps

### Stage 4: Replace backlog scans with claim-based job ownership

- AI worker should stop scanning `content_items` and instead claim explicit AI jobs
- promotion worker should stop scanning all candidates globally and instead claim promotion jobs

### Stage 5: De-synchronize `fetch_news`

- remove inline clustering / promotion / AI from `fetch_news`
- ingestion emits explicit next-step jobs instead
- keep maintenance cron jobs only as safety nets

## First Safe Implementation Step In This Change

This change now implements Stage 1 and the safe part of Stage 2:

- add `content.ai_summary.requested` as a durable outbox event
- enqueue it from readiness synchronization when promoted article/video rows are waiting on AI
- dispatch it through the existing outbox dispatcher
- add `content.promotion_eval.requested` as a durable outbox event
- enqueue it for newly inserted candidate rows from ingestion and signal paths
- dispatch it through the existing outbox dispatcher using the current promotion engine, scoped to the requested content type
- keep the scheduled `ai_retry` job untouched as the rollback path and maintenance sweep
- keep the scheduled `promotion` job untouched as the rollback path and catch-up sweep
- gate the new behavior behind a feature flag so rollout can be staged

This change also lays the Stage 3 foundation:

- `ContentEventDispatcher` now supports optional event-type scoping
- a dedicated long-running outbox worker entrypoint exists in `app/content_event_worker.py`

## Failure Modes And Rollback

### Failure modes

- duplicate AI requests from repeated readiness sync calls
  - mitigated by deduping pending/processing outbox rows per content item
- event-driven AI worker errors
  - mitigated by existing dispatcher retry/backoff
- event handler summary fails on thin article inputs
  - scheduled `ai_retry` remains as catch-up path
- unexpected cache lag
  - `content.ready` events still exist and still drive current invalidation behavior

### Rollback

- disable the feature flag
- leave any already-enqueued events pending until re-enabled or manually drained
- scheduled `ai_retry` remains the authoritative fallback

## Unknowns / Follow-Up Before Stage 2+

- whether `content_event_outbox` should stay the long-term general job queue or whether a dedicated job table is preferable once promotion/AI volume grows
- whether we want semantic uniqueness constraints in the outbox for ready/unready fanout
- whether AI workers need DB ownership columns for observability and stuck-job recovery beyond outbox claim timestamps
- whether promotion needs a dedicated `promotion_jobs` table to prevent large queue fanout for highly active sources
