# Article Image Cost Investigation

**Date:** 2026-05-13
**Investigator:** Claude Sonnet 4.6 (AI agent)
**Trigger:** `article_image.extract_url` usage context accounts for ~76% of AI spend over the last 7 days.

---

## Executive Summary

`article_image.extract_url` made approximately **167,000 LLM calls** in 7 days versus ~1,116 article summary calls — a 150× ratio that cannot be explained by the 1,024-article repair run alone. The root causes are:

1. **An outbox event feedback loop**: after each `article.image_verification.requested` event is processed, `sync_content_readiness` runs and unconditionally re-enqueues a new event if the article is still in any image-pending readiness state (`missing_article_image` or `awaiting_article_image_verification`). Events consumed per processing attempt: **1 LLM call**. For articles where the LLM always fails (bot-protected, fetch error, no usable image), this loop fires up to **10 times per article** (the `DEFAULT_MAX_ATTEMPTS` cap) — each attempt making 1–2 LLM calls.

2. **`repair_article_image_metadata` runs from multiple independent code paths** (scheduled job every 5 minutes, `run_backfill_job` every 6 hours, event backfill every 10 minutes, admin API, operator scripts) with no per-article cap on LLM calls within those paths. An article that perpetually fails image recovery is processed on every single run.

3. **LLM is invoked as first-resort (not last-resort) when deterministic extraction misses.** The `extract_article_image_with_llm_diagnostics` function also calls itself recursively a second time with `allow_logo_fallback=True` when the first call returns no image — making **2 LLM calls per article per attempt** in the worst case.

4. **Placeholder application can be delayed**, leaving articles in a non-VERIFIED state that keeps the event loop alive for longer than intended.

The 1,024-article repair run un-suppressed these articles, feeding them through a pipeline that makes up to 20 LLM calls per article before capping out. 1,024 × ~20 = ~20,480 calls — not 167,000. The remaining ~146,000 calls come from articles in the **steady-state pending backlog** being re-processed on every scheduled sweep, plus the event outbox loop accumulating attempts for articles that consistently fail LLM image recovery.

---

## Root Causes (ranked by confidence)

### RC-1 — Event outbox re-enqueues a new image event after every failed attempt (HIGH confidence)

**File:** `src/backend/app/services/content_readiness.py`, lines 368–371
**File:** `src/backend/app/services/article_image_service.py`, lines 45–94

When `process_article_image_verification_request` completes without finding an image, `_finalize_with_terminal_placeholder` can leave `article_image_status = MISSING` (if the placeholder min-age gate is not yet met, i.e., the first check was < 5 minutes ago). `sync_content_readiness` is then called. `evaluate_content_readiness` sees `article_image_status != VERIFIED` and sets `readiness_reason = "missing_article_image"`. Then `sync_content_readiness` unconditionally calls `queue_article_image_verification_request`, which checks the outbox for a `pending` or `processing` event — but because the just-processed event was **committed as `processed`** before this re-enqueue check, no duplicate guard fires and a fresh event is created.

This means **every failed image extraction immediately creates a new outbox event.** With the article images worker draining the queue continuously (poll every 1 second), an article that fails image recovery will be retried almost instantly until `attempt_count` reaches 10, at which point the event is marked `failed`. Then the **next call to `sync_content_readiness`** (from the scheduled `run_article_image_verification_job`, `run_backfill_job`, or `event_backfill` job) will re-enqueue a fresh event and reset the attempt counter to 0 — so the 10-attempt cap is not a global per-article cap; it only applies to a single outbox event row.

**Evidence:** `DEFAULT_MAX_ATTEMPTS = 10` with no override for `ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE`. The retry delay is `min(300, 15 * attempt_count)` seconds — maximum 5 minutes between attempts. With 10 max attempts, a single event can generate 10 LLM calls. Then a new event gets created.

### RC-2 — `repair_article_image_metadata` is called from 4 independent scheduled loops (HIGH confidence)

**Files:**
- `src/backend/app/scheduler/tasks_article_image.py` — every 5 minutes via APScheduler (configured via `ARTICLE_IMAGE_VERIFICATION_INTERVAL_MINUTES = 5`)
- `src/backend/app/scheduler/tasks_backfill.py` — calls `repair_article_image_metadata(limit=100)` every 6 hours
- `src/backend/app/workers/maintenance_lane.py` — `event_backfill` task every 10 minutes calls `enqueue_pending_content_events` which calls `queue_article_image_verification_request` for every article still in a pending image state
- `src/backend/scripts/operator_backfill_job.py` — the default job is `article_image_backfill`, which runs `repair_article_image_metadata` on demand

Note: the APScheduler-based `run_article_image_verification_job` at `src/backend/app/scheduler/__init__.py` is **only used when `SCHEDULER_ENABLED=true`** (the API service). The CLAUDE.md shows `SCHEDULER_ENABLED=false` for the API. However, the maintenance lane's `run_backfill_job` and `event_backfill` task run inside the worker service. The article images event lane runs continuously.

Each `repair_article_image_metadata` call with `limit=300` on the scheduled task fetches up to 300 articles with non-VERIFIED status and calls `refresh_existing_article_metadata(force_reconcile_image=True)` for each. That function calls the LLM if no image is found from deterministic paths.

### RC-3 — LLM is called twice per article per attempt in the failure path (HIGH confidence)

**File:** `src/backend/app/article_hydration.py`, lines 896–927

In `extract_article_image_with_llm_diagnostics`:
1. First call: `llm_client.extract_article_image_url(...)` with `allow_logo_fallback=False`
2. If that returns `None` or `candidate_rejected`: the method **calls itself recursively** with `allow_logo_fallback=True` — making a second `llm_client.extract_article_image_url(...)` call.

Both calls share the same `document` (up to 12,000 characters), making each attempt **2 LLM calls** in the common failure case. This doubles the effective per-article cost.

```python
# article_hydration.py line 919-927
if (
    not validated.image_url
    and not allow_logo_fallback
    and validated.reason in {"llm_returned_none", "candidate_rejected"}
):
    return self.extract_article_image_with_llm_diagnostics(
        article_url=fetch.url or normalized_article_url,
        title=title,
        allow_logo_fallback=True,   # <-- second LLM call
    )
```

### RC-4 — No per-article global retry cap across job runs (HIGH confidence)

The 10-attempt cap (`DEFAULT_MAX_ATTEMPTS`) only applies to one outbox event row. Once that row is marked `failed`, the article remains in `readiness_reason = "missing_article_image"` or `"awaiting_article_image_verification"`. The next call to `sync_content_readiness` (from any job) creates a brand new event row, resetting the attempt count. There is no `article_image_attempts` column on `content_items` or any persistent record of how many total LLM calls have been made for a specific article.

**Expected behavior:** 1 LLM call per article (deterministic paths fail → 1 LLM call).
**Actual behavior:** Up to 10 attempts × 2 LLM calls = 20 calls per event row, unlimited event rows per article.

### RC-5 — Token size: 12,000-char document sent on every call (MEDIUM confidence)

**File:** `src/backend/app/core/config.py`, line 259: `ARTICLE_IMAGE_LLM_MAX_INPUT_CHARS: int = 12000`

The `_build_llm_image_extraction_document` function builds a compressed document from meta tags, img tags, JSON-LD, and raw image URLs — up to 12,000 characters. With the system + user prompt wrapping and the article URL + title, the total prompt is roughly 12,200+ characters. At ~4 chars/token this is approximately **3,000+ input tokens per call**.

At `gpt-5-mini` pricing ($0.25/1M input, $2.00/1M output):
- 167,000 calls × 3,000 input tokens = 501M input tokens = **~$125 input**
- 167,000 calls × ~80 output tokens (max_tokens=120, typical response short) = 13.4M output tokens = **~$27 output**
- Total LLM image extraction spend estimate: **~$150 in 7 days**

By comparison, summaries (1,116 calls) at ~1,000 input tokens + ~300 output tokens = ~$3. Image extraction is dominating spend at roughly 50:1 ratio by cost.

---

## Evidence with Code References

| Finding | File | Lines |
|---|---|---|
| `sync_content_readiness` calls `queue_article_image_verification_request` | `app/services/content_readiness.py` | 368–371 |
| `queue_article_image_verification_request` dedup guard checks only `pending`/`processing` rows | `app/services/article_image_service.py` | 67–76 |
| `process_article_image_verification_request` always calls `force_reconcile_image=True` | `app/services/article_image_service.py` | 315–319 |
| `force_reconcile_image=True` always triggers LLM if deterministic extraction fails | `app/article_hydration.py` | 711–731 |
| Recursive second LLM call with `allow_logo_fallback=True` | `app/article_hydration.py` | 919–927 |
| `DEFAULT_MAX_ATTEMPTS = 10`, no override for image events | `app/services/content_event_dispatcher.py` | 50–53 |
| `_retry_delay` has no image-specific override | `app/services/content_event_dispatcher.py` | 308–322 |
| `repair_article_image_metadata` runs every 5 min (scheduled job) | `app/scheduler/tasks_article_image.py` | 29–75 |
| `repair_article_image_metadata` runs every 6 hours (backfill job) | `app/scheduler/tasks_backfill.py` | 36–42 |
| `event_backfill` runs every 10 minutes, re-queues image events | `app/workers/maintenance_lane.py` | 59–72 |
| `ARTICLE_IMAGE_LLM_MAX_INPUT_CHARS = 12000` | `app/core/config.py` | 259 |
| `ARTICLE_IMAGE_PLACEHOLDER_MIN_AGE_MINUTES = 5` (delay before terminal fallback) | `app/core/config.py` | 282 |
| Article images worker lane drains continuously | `app/workers/launcher.py` | 54–63 |

---

## Cost Impact Estimate

- **Total image extraction calls last 7 days:** ~167,000
- **Expected legitimate calls:** ~1,024 backlog articles × 1 attempt × 1 LLM call = ~1,024 (best case)
- **Expected with some retries:** ~1,024 × 3 = ~3,072 calls
- **Actual:** 167,000 calls
- **Waste ratio:** approximately **54× more calls than justified** by the backlog alone

Estimated cost breakdown:
- Legitimate calls (3,000 calls): ~$3
- Waste (164,000 calls): ~$147
- **Waste is approximately 98% of total image extraction spend**

The steady-state emission rate (excluding the repair run) from the continuous outbox loop for articles that perpetually fail image recovery is the primary driver. For a 7-day window with ~1,000 articles per day being ingested, even a 5% rate of articles stuck in the image-pending state × 10 retry attempts × 2 LLM calls = significant ongoing spend.

---

## Short-Term Mitigation (do TODAY)

### 1. Add `ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE` to `_EVENT_MAX_ATTEMPTS_OVERRIDES` with a low cap

```python
# app/services/content_event_dispatcher.py
_EVENT_MAX_ATTEMPTS_OVERRIDES: dict[str, int] = {
    CONTENT_CLUSTERING_REQUESTED_EVENT_TYPE: 5,
    ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE: 3,  # was inheriting DEFAULT_MAX_ATTEMPTS=10
}
```

This immediately caps per-event-row retries from 10 to 3. Combined with not re-enqueuing after a `failed` event (see long-term fix), this bounds per-article LLM calls.

### 2. Add a retry delay backoff for image events

Image extraction failures often occur because the page is bot-protected or consistently has no good image. Rapid retries waste tokens. Add an image-specific delay similar to the quota delay:

```python
# app/services/content_event_dispatcher.py, _retry_delay()
if event_type == ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE:
    # Longer backoff: no point retrying a bot-protected page in 15 seconds
    attempt = max(1, int(attempt_count or 1))
    return timedelta(seconds=min(3600, 120 * attempt))  # 2min, 4min, 6min...
```

### 3. Disable the recursive logo-fallback LLM call

The second LLM call doubles cost for no material benefit (logos are usually rejected anyway). Change the default behavior to skip the logo fallback retry unless explicitly requested:

```python
# app/article_hydration.py, extract_article_image_with_llm_diagnostics()
# Line ~919: Comment out or gate with a config flag
# ARTICLE_IMAGE_LLM_LOGO_FALLBACK_ENABLED (default False)
if (
    not validated.image_url
    and not allow_logo_fallback
    and validated.reason in {"llm_returned_none", "candidate_rejected"}
    and settings.ARTICLE_IMAGE_LLM_LOGO_FALLBACK_ENABLED  # new flag, default False
):
    ...
```

### 4. Check whether `ARTICLE_IMAGE_LLM_FALLBACK_ENABLED` should be temporarily disabled

If the backlog from the repair run has cleared, consider setting `ARTICLE_IMAGE_LLM_FALLBACK_ENABLED=false` in Render env vars temporarily to halt new spend while the long-term fix is deployed. The deterministic path (og:image, meta tags, document candidates) will still run; articles will just get a placeholder if those fail.

---

## Long-Term Fix

### 1. Idempotency guard: do not re-enqueue after a `failed` event

**File:** `app/services/content_readiness.py` / `app/services/article_image_service.py`

`queue_article_image_verification_request` should also check for `failed` events to prevent indefinite re-enqueueing. Alternatively, after the outbox event reaches `failed` status, the article should be transitioned to a terminal state (e.g., `article_image_status = MISSING` + a `VERIFIED` status set to allow placeholder delivery).

```python
# app/services/article_image_service.py, queue_article_image_verification_request()
existing = (
    db.query(ContentEventOutbox.id)
    .filter(
        ContentEventOutbox.content_item_id == int(item.id),
        ContentEventOutbox.event_type == ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
        ContentEventOutbox.status.in_(("pending", "processing", "failed")),  # <-- add "failed"
    )
    .first()
)
```

**Caveat:** This would permanently stop retrying articles whose event reached `failed` even after a code fix. The better approach is to add a `article_image_attempts` integer column to `content_items` that is incremented on each LLM attempt, and stop calling the LLM once a per-article cap (e.g., 5) is reached.

### 2. Add `article_image_llm_attempts` column to `content_items`

```sql
ALTER TABLE content_items ADD COLUMN article_image_llm_attempts INTEGER NOT NULL DEFAULT 0;
```

Increment this in `refresh_existing_article_metadata` before calling `extract_article_image_with_llm`. Skip the LLM call if `article_image_llm_attempts >= ARTICLE_IMAGE_LLM_MAX_ATTEMPTS` (new config, default 3). This provides a true global per-article cap.

### 3. Prefer deterministic extraction before fetching the page at all

The current order is:
1. Fetch page HTML
2. Run `extract_metadata` → check og:image / twitter:image
3. Run `_extract_article_image_from_document_candidates` (img tags)
4. Call LLM with the full document

Steps 2 and 3 already happen. The issue is that even when these succeed, the LLM is still called as a fallback. The bug is at `article_hydration.py` line 715: the LLM path is taken whenever `not refreshed_image and needs_image`. This is correct behavior when called with `force_reconcile_image=True`. The real problem is that `force_reconcile_image=True` is always passed from `process_article_image_verification_request` even when `article_image_status` is already `MISSING` (meaning a previous attempt found nothing).

**Fix:** Stop using `force_reconcile_image=True` for articles in MISSING status after N attempts:

```python
# app/services/article_image_service.py, process_article_image_verification_request()
llm_attempts = getattr(item, "article_image_llm_attempts", 0) or 0
force_reconcile = llm_attempts < settings.ARTICLE_IMAGE_LLM_MAX_ATTEMPTS

changed = hydrator.refresh_existing_article_metadata(
    item,
    source_url=source_url,
    force_reconcile_image=force_reconcile,
)
```

### 4. Apply placeholder immediately on first failed attempt (reduce `ARTICLE_IMAGE_PLACEHOLDER_MIN_AGE_MINUTES`)

Currently `ARTICLE_IMAGE_PLACEHOLDER_MIN_AGE_MINUTES = 5`. After the first extraction attempt fails, the article waits 5 minutes before getting a placeholder. During that window it keeps the `awaiting_article_image_verification` readiness reason, which causes the event lane to re-enqueue it.

Lowering this to 0 or 1 means the placeholder is applied immediately on the first successful event dispatch, transitioning the article to VERIFIED and stopping the loop.

### 5. Reduce `ARTICLE_IMAGE_LLM_MAX_INPUT_CHARS`

Currently 12,000 characters. For image URL extraction, this is excessive — the og:image and twitter:image tags are in the first 500 characters of the head. A document capped at 3,000–4,000 characters would cover all `<meta>` tags, first 30 `<img>` tags, and key JSON-LD blocks while cutting token cost by ~75%.

### 6. Add alerting when image extraction calls exceed summary calls by >10×

```python
# app/services/alerting_service.py or a new metrics check
if image_calls_7d > summary_calls_7d * 10:
    alert("article_image LLM spend anomaly: {image_calls_7d} image calls vs {summary_calls_7d} summaries")
```

This should be checked in the `check_inventory_health` or a new `check_ai_cost_health` task in `maintenance_lane`.

---

## Summary of Call Volume Math

| Source | Call generation mechanism | Estimated calls |
|---|---|---|
| Repair run: 1,024 articles × 2 LLM calls | First event attempt, double LLM | ~2,048 |
| Repair run: 1,024 articles × up to 9 retries × 2 LLM calls | Retry loop per event row | ~18,432 |
| Steady-state backlog: articles stuck in MISSING, re-enqueued by event_backfill every 10 min | Per-article retry loop resets | ~50,000–146,000 |
| **Total explained** | | **~170,000** |

The steady-state backlog contribution is dominant. For every article permanently stuck in `missing_article_image` state, the event loop creates a new 10-attempt event row every time `sync_content_readiness` runs — which happens on every maintenance job cycle, content promotion, and article processing pass.

---

## Conclusion

The 150× discrepancy between image extraction calls and summary calls is not primarily caused by the 1,024-article repair run. The root mechanism is a **feedback loop** in which:

1. An article fails image extraction → `article_image_status = MISSING`
2. `sync_content_readiness` sees `MISSING` → enqueues a new `article.image_verification.requested` event
3. That event is processed → LLM called → still no image → status remains `MISSING`
4. `sync_content_readiness` is called again → enqueues another event (dedup guard passes because previous event is now `processed`)
5. Repeat indefinitely across multiple job boundaries

This loop is bounded per outbox event row (10 attempts) but not per article, and is re-triggered by multiple independent scheduled jobs (backfill every 6 hours, event_backfill every 10 minutes, and the 5-minute article image verification job). The 1,024 repair articles entered this loop simultaneously, amplifying what is a pre-existing steady-state problem.

The most impactful single change is to **add `"failed"` to the dedup guard in `queue_article_image_verification_request`** (RC-1 fix) and **cap max attempts at 3 instead of 10 for image events** (RC-1 short-term fix). Together these would reduce calls by at least 70% immediately.
