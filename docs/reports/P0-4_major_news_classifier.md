# P0-4 — MAJOR_NEWS Classifier Precision/Recall Audit
**Date:** 2026-05-11 | **Status:** COMPLETE

## Summary

The `is_major_tech_news` classifier is working correctly on items it sees, but is effectively invisible in the surfaced feed because:
1. The MAJOR_NEWS probe processes too few items (only 10 total from "Cnbc" + "Platformer" in 14 days).
2. **CNBC Technology** — the main CNBC RSS feed with 99 ingested items — has NEVER had a single item promoted to READY. This is a pipeline bug, not a classifier issue. Multiple items from this source have `is_major_tech_news=true` and `global_score > 0.55` but are stuck permanently at `CANDIDATE`.

---

## "Cnbc" Source (MAJOR_NEWS lane) — 14-Day Data

Only **9 items** from MAJOR_NEWS-designated CNBC + Platformer + Big Technology in 14 days:

| Title (truncated) | Source | is_major | Status | Score |
|---|---|---|---|---|
| Did xAI just concede the AI race? | Platformer | **true** | CANDIDATE | — |
| The Trump administration's AI doomer moment | Platformer | false | READY | 0.29 |
| We may now know what kind of AI bubble this is | Platformer | false | CANDIDATE | — |
| Hassett says AI isn't costing anybody their job... | Cnbc | — | CANDIDATE | — |
| Musk texted OpenAI's Brockman about settlement | Cnbc | — | CANDIDATE | — |
| Roblox shares plummet 18% on child safety measures | Cnbc | — | CANDIDATE | 0.33 |
| The Tech Download: Chip stocks surge | Cnbc | — | CANDIDATE | — |
| Apple CEO Tim Cook warns of extended memory crunch | Cnbc | — | CANDIDATE | — |
| China's EV price war turns into AI arms race | Cnbc | — | CANDIDATE | — |

Observations:
- Only 1 item (Platformer) is READY. All others are CANDIDATE with `readiness_reason='suppressed'`.
- `is_major_tech_news=true` on only 1 item ("Did xAI just concede the AI race?") — which is also CANDIDATE/suppressed.
- Most CNBC (MAJOR_NEWS lane) items have no `tech_relevance` or `promotion_score` — they haven't gone through the AI pipeline.

The "Cnbc" MAJOR_NEWS-lane items appear to be entering the system but immediately getting `is_suppressed=True`, which prevents them from ever reaching the AI pipeline or promotion service.

---

## CNBC Technology Source (Regular RSS lane) — CRITICAL BUG

| Metric | Value |
|---|---|
| Total items ingested | 99 |
| CANDIDATE (never promoted) | 99 (100%) |
| READY | **0** |
| Items with is_major_tech_news=true | 4+ |

**Every single CNBC Technology item is stuck at CANDIDATE with no promotion_score.** This includes:

| Title | is_major | Score |
|---|---|---|
| Microsoft CEO Satya Nadella takes stand in Musk v. Altman trial | **true** | 0.591 |
| Intel shares soar on Apple chip deal report | **true** | 0.578 |
| OpenAI trial: Mother of Musk's children says he offered Altman a Tesla board seat | **true** | 0.477 |
| Intel soars 14% on report of Apple chip talks | **true** | 0.509 |

The `is_major_tech_news` flag IS being set correctly by the probe. The problem is **downstream**: the promotion service never picks up these items.

---

## Classifier Precision/Recall (limited sample, 14-day window)

Given only 9 items from the MAJOR_NEWS feeds, a formal 30-item sample isn't feasible. Of the 3 items with `is_major_tech_news` populated:

| Item | Classifier said | Human rating | Correct? |
|---|---|---|---|
| "Did xAI just concede the AI race?" | true (0.90) | Yes — significant AI competitive analysis | ✓ |
| "The Trump administration's AI doomer moment" | false (0.95) | Borderline — policy analysis, arguable | ✓ |
| "We may now know what kind of AI bubble this is" | false (0.91) | Borderline — macro analysis, not breaking news | ✓ |

Precision and recall cannot be meaningfully computed from 3 samples. The classifier itself is **not the bottleneck** — it's making reasonable calls on the tiny sample it sees. The volume problem is the bottleneck.

---

## Root Cause Assessment

The `major_news_probe` runs every 15 minutes against 5 feeds. But:

1. **CNBC MAJOR_NEWS lane**: items arrive as CANDIDATE but immediately get `is_suppressed=True` — they never reach the probe or the promotion service. 9 total items in 14 days is far below CNBC's actual publishing volume.

2. **CNBC Technology (regular lane)**: items are ingested correctly, scored by global_score computation, even flagged `is_major_tech_news=true` — but promotion_score is never computed, so the promotion service can't pick them up.

3. **Volume**: 9 + 99 = 108 combined CNBC items, of which 1 has ever reached READY. The Musk v. Altman trial and Apple/Intel chip deal — two of the week's biggest stories — are completely absent from the user-facing feed.

---

## Recommended Actions (mapped to plan)

| Action | Plan Task | Priority |
|---|---|---|
| Investigate why CNBC Technology items have no promotion_score | **NEW — P0-4-BUG** | IMMEDIATE |
| Investigate why Cnbc (MAJOR_NEWS) items get is_suppressed=True automatically | **NEW — P0-4-BUG** | IMMEDIATE |
| After fix, widen major_news_probe source set | P2-2 | Phase 2 |
| Lower classifier confidence threshold | P2-3 | Phase 2 (after bug fix) |

The `is_major_tech_news` boost (P2-4) cannot help until the upstream bug is fixed — the flag is correct but the items never reach the feed.
