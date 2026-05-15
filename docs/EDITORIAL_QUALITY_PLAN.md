# Editorial Quality & Gap-Closure Plan

**Created:** 2026-05-11
**Status:** Not started — Phase 0 (validation) should run before any code change.
**Owner:** Unassigned
**Source of diagnosis:** Editorial audit of last-48h `READY` articles vs. independent benchmark of trending tech news. See section "Background" below for the gaps that motivated this plan.

This file is a working tracker. Update the `Status` column inline as work progresses. Each task is self-contained — an agent picking this up cold should be able to read the task row, the linked code path, and the acceptance criterion and execute without further context.

---

## How to use this file

- Tasks are grouped by Phase. **Do Phase 0 first** — it validates whether the rest of the plan is correctly targeted.
- `Status` values: `todo` / `in-progress` / `blocked` / `done` / `skipped (reason)`.
- When marking a task `done`, add a `Result:` line under the row with a one-line summary of what shipped or what the script reported.
- When a Phase-0 script result contradicts the assumption behind a Phase-1/2/3 task, mark that task `blocked` and add a `Note:` line.
- Do not delete tasks. If a task is abandoned, mark `skipped` with the reason.

---

## Background — gaps the plan exists to close

Audit of 50 highest-scored `READY` articles in last 48h (2026-05-09 → 2026-05-11) found:

- **~50% of top-50 was filler or weak** (affiliate deals, listicles, hobbyist content, unofficial game ports, personal blogs).
- **Same-story duplicates leaking through clustering**: "Claude Platform on AWS" ranked 3× (TechCrunch/AWS blog/The New Stack); Google AI zero-day ranked 3× (Engadget/Digital Trends/TheHackerNews); Anthropic blackmail story 2×.
- **Major stories of the window missing entirely**: Pentagon designating Anthropic supply-chain risk (CNBC); Apple↔Intel/Samsung chip talks (Bloomberg); Helsing ~$1.2B raise; OpenAI GPT-5.5-Cyber EU rollout; SoftBank Sakai battery factory; Config robotics-data startup; US Senate AI commission proposal.
- **Apple, Microsoft, Meta**: zero stories in top-50.
- **Funding/VC**: zero stories in top-50 despite `BUSINESS` and `FUNDING`-adjacent feed roles existing.
- **Source skew**: heavy reliance on Android Authority, ZDNet, CNET, AWS company blog; Bloomberg/Reuters/FT not in registry at all; The Information enabled but 403-dead.
- **`is_major_tech_news=true` count in top-50**: 0. The flag is effectively unused because `major_news_probe` only fans out across 5 feeds.

Root-cause map (verified against code):

| Symptom | Root cause | Code reference |
|---|---|---|
| Same story 3× | Clustering combined-score threshold 0.50 too strict; different headline wordings drop below | `app/config/clustering.py` thresholds; `app/clustering/dedupe.py` SimHash |
| Deal/affiliate posts in top-50 | No title-pattern filter anywhere | nothing exists; `editorial_repo.suppress_item` is manual-only |
| Bloomberg/Reuters stories missing | Feeds not in registry | `app/integrations/rss_feeds.py` |
| The Information stories missing | Feed enabled but returning 403 | `rss_feeds.py` (notes line); `source_fetch_states` table |
| Apple/MSFT/Meta missing | Apple PRIMARY feed `enabled=False`; OpenAI PRIMARY `enabled=False`; Meta AI `enabled=False` | `rss_feeds.py` |
| `is_major_tech_news=true` always false in surfaced items | `major_news_probe` only probes 5 sources; everything else bypasses major-news classifier | `app/scheduler/tasks_major_news.py`, `app/workers/maintenance_lane.py` |
| Android Authority overweight | Per-source cap is window-based (max 2 per 5-item window) → up to 20/50 from one source possible | `app/services/playlist_service.py` MAX_SOURCE_PER_WINDOW |
| Funding stories ranked low | No co-mention boost, no category floor, regex topic extraction misses VC entities | `app/ranking/global_score.py`; `app/ingestion/extractors.py` |

---

## Phase 0 — Validation (read-only, no production change)

Goal: confirm the diagnosis with data before changing any pipeline code. Each script reads from prod via `render psql` (or fetches public RSS) and produces a short markdown report. **No writes.**

| ID | Task | Files / endpoints | Acceptance criterion | Status |
|---|---|---|---|---|
| P0-1 | **Affiliate-regex backtest.** | `render psql` → `content_items.title` last 30d. Report: `docs/reports/P0-1_affiliate_regex.md`. | FP rate <5% on sample. | **done** — Result: 201 hits/30d, ~95% TP. Bare `deal`/`lifetime` need scoping. Revised safer regex documented in report. |
| P0-2 | **Source-coverage gap test.** | Techmeme RSS + `render psql`. Report: `docs/reports/P0-2_source_gap.md`. | Source/ingested/ranked table. | **done** — Result: 27% of Techmeme top stories reach READY. CNBC Technology bug discovered (99 items, 0 READY). Techmeme feed confirmed as highest-ROI addition. |
| P0-3 | **Dedup leak audit.** | `render psql` + similarity analysis. Report: `docs/reports/P0-3_dedup_leak.md`. | Threshold safety assessment. | **done** — Result: 54.6% of articles unclustered. Threshold 0.40 is safe but won't catch semantic dupes (different wordings). P2-1 still needed. |
| P0-4 | **MAJOR_NEWS classifier precision/recall audit.** | `render psql` + manual rating. Report: `docs/reports/P0-4_major_news_classifier.md`. | Precision/recall + root cause. | **done** — Result: Classifier itself is fine. Root cause: CNBC (MAJOR_NEWS lane) items get `is_suppressed=True` immediately; CNBC Technology (regular RSS) never gets `promotion_score` computed. Both stuck upstream of the classifier. |
| P0-5 | **AWS-blog ingestion path trace.** | Codebase grep + `render psql`. Report: `docs/reports/P0-5_aws_ingestion_trace.md`. | Actual ingestion path identified. | **done** — Result: AWS ML blog (`/blogs/machine-learning/`) is `enabled=True` in AI role — working as configured. Fix is vendor-domain cap at playlist layer (P1-6), not registry change. |

### Phase 0 Key Results (read before starting Phase 1)

| Report | Critical finding | Impact on plan |
|---|---|---|
| P0-1 | Regex concept sound; bare `deal`/`lifetime` cause ~5% FP rate (catches Apple/Intel chip deal, etc.). Use revised targeted regex from report. | P1-5: use revised regex from P0-1 report, not the original proposal. |
| P0-2 | **CNBC Technology: 99 items ingested, 0 READY — pipeline bug.** Many key stories (RCS E2EE, Apple/Intel, Musk trial) are in DB but stuck at CANDIDATE. The gap is promotion blockage, not just absent sources. | Adds **P0-BUG-1** (immediate). P1-1 (Techmeme) confirmed essential. |
| P0-3 | 54.6% unclustered. Threshold 0.40 is safe; lowers to catch Cluster A/C style dupes. Semantic dupes (Cluster B: Google zero-day 3×) require P2-1. | P1-7 is safe to ship. P2-1 confirmed necessary. |
| P0-4 | Classifier working correctly. Both CNBC ingestion paths are broken *upstream* of the classifier. `is_major_tech_news=true` is set on items that can never reach the feed. | P2-3/P2-4 depend on P0-BUG-1 fix first. |
| P0-5 | AWS ML blog is the vendor-content source; intentionally enabled. Fix is playlist-layer domain cap, not feed disabling. | P1-6 (per-source cap) handles this. |

**Phase 0 exit criterion: MET.** One urgent bug task added — must be resolved before Phase 1 improvements fully land.

---

## P0-BUG-1 — CNBC Technology pipeline blockage (URGENT — do before Phase 1)

**Priority: highest.** This single bug is suppressing 99 queued articles including multiple `is_major_tech_news=true` stories (Musk v. Altman trial, Apple/Intel chip deal). Fixing it will immediately surface important content.

| Sub-task | What to investigate | Files |
|---|---|---|
| A | Why do CNBC Technology items have `promotion_score=NULL`? Is the source filtered out of the promotion service's CANDIDATE fetch query? | `app/scheduler/tasks_promotion.py` — find the CANDIDATE fetch, check if source name or feed role is excluded. |
| B | Why do items from the "Cnbc" MAJOR_NEWS lane get `readiness_reason='suppressed'` / `is_suppressed=True` immediately on ingestion? Is there an auto-suppress rule for CNBC URLs (paywall detection, domain blocklist)? | `app/services/promotion_service.py`, `app/ingestion/checkpoint_worker.py`, any `is_suspicious_url()` or paywall-detection logic. |
| C | Once root cause found: fix, deploy, verify CNBC Technology items flow through to READY within one pipeline cycle. | — |

**Acceptance criterion:** Within 24h of deploy, ≥5 CNBC Technology items reach `readiness_status=READY`. The Musk v. Altman and Apple/Intel stories currently at CANDIDATE should surface.

**Status: done — deployed in commit c440014**

### What was done

- **Root cause**: `article_unskimmable_service` was permanently suppressing paywalled articles (`CNBC Technology`, `BleepingComputer`, `OpenAI`, etc.) after max extraction retries, even when `item.description` had 20-50 words of usable content.
- **Fix**: Added description-fallback path in `content_ai_service.py` (~line 283). If `len(description.split()) >= 20` after the tech-relevance check, use the description as `summary_input` and fall through to the normal LLM summarization path instead of retrying/terminating.
- **Repair script**: `scripts/repair_unskimmable_with_description.py` — un-suppresses qualifying items so they re-enter the promotion pipeline.

### Post-deploy repair run order (run after deploy confirms working)

Use `POST /api/v1/admin/trigger-scripted-maintenance` — no local setup or DATABASE_URL needed.

1,054 qualifying items across 90 days. Run in order, highest-value first:

```bash
export HOST="https://blips-api.onrender.com"
export ADMIN_KEY="your-admin-api-key"

# Step 1 — dry run all sources to verify counts
curl -s -X POST "$HOST/api/v1/admin/trigger-scripted-maintenance" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"payload": {"job": "repair_unskimmable_with_description", "dry_run": true}}'

# Step 2 — high-value news sources first (run each; confirm repaired > 0 before next)
for SOURCE in "CNBC Technology" "Bleepingcomputer" "Openai" "Infoq" "Darkreading" \
              "TechCrunch" "Wired" "The Verge" "Simonwillison" "Bigtechnology"; do
  curl -s -X POST "$HOST/api/v1/admin/trigger-scripted-maintenance" \
    -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
    -d "{\"payload\": {\"job\": \"repair_unskimmable_with_description\", \"source\": \"$SOURCE\"}}"
  echo ""
done

# Step 3 — broader sweep (optional, run next day)
curl -s -X POST "$HOST/api/v1/admin/trigger-scripted-maintenance" \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"payload": {"job": "repair_unskimmable_with_description", "limit": 500}}'
```

Supported options in the `payload` object: `source` (string), `dry_run` (bool), `limit` (int, default 500), `min_desc_words` (int, default 20), `lookback_days` (int, default 90).

---

## Phase 1 — Cheap, high-leverage code changes

Only start after Phase 0 reports are in. Each task is independent — they can ship in any order.

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P1-1 | **Add Techmeme RSS to registry as MAJOR_NEWS role, PREMIUM tier, weight 0.90.** Techmeme aggregates Bloomberg/Reuters/FT/WSJ tech headlines; single feed gives 80% of the missing-source coverage at zero cost. | `app/integrations/rss_feeds.py` — add `FeedConfig(url="https://www.techmeme.com/feed.xml", name="Techmeme", role=MAJOR_NEWS, quality_tier=PREMIUM, ...)` | After deploy + one ingestion cycle, ≥5 Techmeme items appear in `content_items` with `readiness_status=READY` within 24h. | **done** — Result: Added as 6th MAJOR_NEWS feed (PREMIUM, weight=0.90, daily_cap=5, decay=FAST). Bumped `MAJOR_NEWS_PROBE_MAX_FEEDS` default from 5→6 in `tasks_major_news.py` so the probe actually reaches it. **Known limitation**: Techmeme RSS `<link>` is always a Techmeme anchor page URL, not the original article. `source_url` in DB will be `techmeme.com/...` — users tap "Read more" and land on Techmeme, not WSJ/Bloomberg/Reuters. Acceptable short-term; fix tracked in P1-1b below. |
| P1-1b | **Extract original article URL from Techmeme RSS description.** Techmeme embeds the real article URL as the first non-Techmeme `<A HREF>` in its RSS description HTML. Parse this and use it as `source_url` instead of the Techmeme anchor page. | `app/integrations/rss_client.py` — add optional `primary_link_from_description: bool` to `FeedConfig`; when True, extract the first external link from description HTML and use it as `entry.url`. Set flag on Techmeme `FeedConfig`. | After deploy, `source_url` for Techmeme items points to WSJ/Bloomberg/Reuters. Tapping "Read more" navigates to original article. | **done** — Result: `primary_link_from_description: bool = False` field added to `FeedConfig`; `fetch_feed()` in `rss_client.py` extracts first non-aggregator `<a href>` from description HTML when flag is True. Flag set on Techmeme entry. Bug found and fixed in same session: the major-news probe called `fetch_feed()` directly without forwarding the flag (P6-3), so all Techmeme items kept `techmeme.com/*` URLs until the probe was patched. |
| P1-2 | **Investigate and fix The Information 403.** Currently `enabled=True` but returning 403. Likely UA-related (extraction fetcher uses browser UA already; ingestion's `rss_client` may not). Check `source_fetch_states` for failure mode. | `app/integrations/rss_client.py` (User-Agent header) or `rss_feeds.py` (alternate feed URL). Verify by running `python -c "import feedparser; ..."` against the URL with the prod UA. | At least 1 successful fetch logged in `source_fetch_states`; ≥1 item ingested within 48h. If feed requires auth and can't be fixed for free, mark `skipped (paid subscription required)` and re-disable. | **skipped** — Root cause: Render egress IPs are IP-blocked by The Information's CDN (Cloudflare). Feed returns 200 from non-cloud IPs; returns 403 from Render. Not a UA issue — UA rotation cannot fix an IP-level block. Notably, the feed provides FULL article HTML (not paywalled teasers), making it very high-value if accessible. Fix requires a residential/non-Render fetch proxy, which adds infra cost and complexity. Mitigation: Techmeme already surfaces some Information articles. Revisit if a fetch-proxy layer is added to the infrastructure. |
| P1-3 | **Re-enable Apple PRIMARY feed.** Currently `enabled=False` in registry. Confirm whether disable reason is documented; if not, re-enable behind a `daily_cap` of 5. | `app/integrations/rss_feeds.py` — flip `enabled=True` on the Apple PRIMARY entry. | After 48h, ≥3 Apple-newsroom items in `content_items`. If feed is dead/spammy, revert and mark `skipped` with reason. | **done** — Re-enabled. Kept `daily_cap=2` (not 5 as originally planned) because Apple publishes 2-3 items/week; a cap of 5 risks PR floods on product event days. Feed contains genuine news (CEO succession, RCS E2EE, WWDC, earnings) alongside promo fluff (Arcade, F1, Pride merch) — tech-relevance classifier will filter promo. Apple newsroom URLs are open, so article extraction will succeed. |
| P1-4 | **Re-enable OpenAI Blog and Meta AI feeds** (separately track each). | `rss_feeds.py` — flip `enabled=True`. | Each produces ≥1 item per week, or revert. | **done** — OpenAI: `/blog/rss.xml` 307-redirects to `/news/rss.xml`, which is already enabled in the AI role — kept disabled, renamed to "(legacy)", no duplicate ingestion needed. Meta AI: original `ai.meta.com/blog/rss/` is 404-dead; replaced with `engineering.fb.com/feed/` (200, STANDARD tier, weight=0.80, daily_cap=2) — covers AI agents, infra, crypto. RSS descriptions are 79-82 words (extraction also open/available). |
| P1-5 | **Affiliate/deals title-pattern suppression at promotion.** Implement `_is_affiliate_or_deal_title(title: str) -> bool` using the **revised** regex from P0-1 report (not the original broad proposal — bare `deal`/`lifetime` cause FPs on Apple/Intel stories). Gate via `PROMOTION_DEAL_SUPPRESSION_ENABLED` config flag. If matched: `is_suppressed=True`, `curation_status=REJECTED`, reason `"affiliate_or_deal_title"`. Also add `9To5Toys` to a `DEALS_SOURCE_BLOCKLIST`. | `app/services/promotion_service.py`; `app/core/config.py`. Unit test in `tests/services/test_promotion_service.py`. | After deploy, zero regex-matching titles in READY over 7-day window. Zero FPs on business-deal stories (Apple/Intel type). | **done** — Implemented in `article_quality_policy.py` (not promotion_service — wired into `classify_article_quality_block` so it gates both promotion and READY SQL filter). `ARTICLE_DEAL_SUPPRESSION_ENABLED=True` flag in config. 11 affiliate patterns + `9to5toys` source blocklist. 32-test suite covering all TPs, 9 business-deal FP cases, manual_added bypass, and feature-flag disabled path. SQL defense-in-depth added to `article_quality_sql_allow_filter()`. Note: `%% off%` excluded from SQL filter — SQLAlchemy `.like()` treats `%` as wildcard (not escapable), so the numeric-percent pattern is Python-only. |
| P1-6 | **Global per-source cap in playlist diversity mixer.** Add `MAX_PER_SOURCE_PER_PLAYLIST = 3` constant. After window-based interleaving, drop any source's 4th+ item from the candidate set. | `app/services/playlist_service.py` (or `diversity_mixer.py` if separate). Adjust constant near line 65–70 where existing source caps live. Unit test. | In a sampled playlist of 50, no source appears more than 3 times. Validate by hitting `GET /session/playlist?size=50` on a test device 10× and counting. | **done** — Added `MAX_PER_SOURCE_PER_PLAYLIST = 3` constant; added `source_total_counts` tracker in `_select_diverse_items`; global cap checked before the rolling-window cap (fail-fast on heavy sources). 3 new tests added to `test_playlist_service_diversity_caps.py` (hard limit enforced, multiple sources each allowed up to cap, playlist still fills when enough sources exist). The pre-existing rolling-window cap (`MAX_SOURCE_PER_WINDOW = 2` in 5-item window) is retained alongside this new global cap. |
| P1-7 | **Lower clustering combined-score threshold from 0.50 → 0.40.** P0-3 confirmed this is safe — 0 false-merges in 20-pair sample. Will close Cluster A/C style dupes (same entities, different headline wordings). Does NOT close Cluster B (Google zero-day, fully different vocabularies) — that requires P2-1. | `app/config/clustering.py` — change `CLUSTER_COMBINED_THRESHOLD`. | After deploy + 48h, cluster count with ≥2 members grows by ≥30%; 20-cluster spot-check shows no false merges. | **done** — Changed `combined_threshold` default from 0.5 → 0.4 in `ClusteringConfig`. Near-duplicate SimHash path unchanged (bumps score to threshold + 0.10 = 0.50, same absolute floor as before). Overridable at runtime via `CLUSTERING_COMBINED_THRESHOLD` env var. Existing near-duplicate tests pass. |
| P1-8 | **Listicle title penalty.** Add `_listicle_penalty(title) -> float` that returns -0.15 if title matches `^\d+\s+(best\|top\|reasons\|tips\|gadgets\|fitness)\b`. Wire into promotion_score or quality_score. | `app/ranking/quality.py` or `app/services/promotion_service.py`. Unit test. | After deploy, listicle-pattern items' average `global_score` drops by ≥0.10 vs prior 14-day baseline. | **done** — Added `listicle_penalty(title)` and `LISTICLE_PENALTY = 0.15` to `app/ranking/quality.py`; applied in `compute_quality_score` before final clamp. Net global_score effect: -0.06 (quality_weight=0.40 × 0.15). 16-test suite covers all 6 keyword TPs, 4 FP cases (no leading number, mid-title number, "Fortune 500", normal news), None/empty safety, and that `quality_score` never goes negative. |

---

## Phase 2 — Medium-effort changes

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P2-1 | **Cross-source fuzzy-title dedup at promotion** as a backstop to clustering. Before marking an item `PROMOTED`, check if any other item in last 48h with `curation_status IN (PROMOTED, READY)` has token-set similarity ≥0.75 to this one's title AND shares ≥1 entity. If yes, mark new item `is_suppressed=True` with reason `"cross_source_duplicate"` (keep the higher-`base_quality_weight` one). | `app/services/promotion_service.py` — new check between current dedup penalty and final commit. | After deploy, count of near-duplicate triples (3+ items <48h apart with ≥0.75 title similarity) in `READY` set drops to 0 in 7-day window. | done |
| | Result: Added `_title_token_similarity` (Jaccard over word tokens), `_normalize_entities_for_dedup`, `_is_cross_source_duplicate` helpers and `_get_recently_promoted_dedup_set()` method. Wired into `_promote_type()` for ARTICLE type only — after cap checks, before promoting. Decision: **soft skip only** (no `is_suppressed=True`), item stays CANDIDATE so threshold miscalibration is recoverable; the dedup check re-fires each run until the original ages out of the 48h window. `seen_dedup_pairs` is seeded from DB pre-load + extended per newly-promoted item in the same run (catches within-run duplicates too). Items with no entities on either side are never suppressed. `CROSS_SOURCE_DEDUP_ENABLED=True` feature flag. 27 new unit tests (all passing). | | |
| P2-2 | **Widen `major_news_probe` source set.** Currently probes 5 feeds. Add TechCrunch front-page, The Verge front-page, Ars Technica, Reuters Technology, and the Techmeme feed from P1-1. The LLM classifier itself is the editorial layer; expanding candidate pool is cheap. | `app/scheduler/tasks_major_news.py` (source list); `app/integrations/rss_feeds.py` (mark these dual-role MAJOR_NEWS). | After deploy, ≥20% of `READY` items from these sources carry `is_major_tech_news=true` within 7-day window. | done |
| | Result: Reuters RSS returns 401 on all endpoints. TechCrunch/The Verge/Ars Technica already ingest via BREAKING role — the probe's `on_conflict_do_nothing` means the flag is never set on pre-existing items. Solution: added `_retro_classify_premium_breaking()` pass that runs after the main feed loop, querying recently-promoted BREAKING items from these sources where `is_major_tech_news IS NULL` and running the same two-stage classifier. Added `MAJOR_NEWS_RETRO_CLASSIFY_SOURCES` constant. Probe result dict now includes `retro_classified` + `retro_major`. 8 new unit tests (all passing). | | |
| P2-3 | **Lower MAJOR_NEWS classifier confidence threshold from 0.55 → 0.45.** P0-4 found classifier is NOT the bottleneck — the items are blocked upstream. Only do this after P0-BUG-1 is fixed and the probe is actually seeing reasonable volume. | `app/services/major_news_constants.py` — `MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE`. | After deploy, recall on a fresh 30-item hand-rated sample improves to ≥60% with precision still ≥80%. | done |
| | Result: One-line change. `MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE` lowered from 0.55 → 0.45 in `major_news_constants.py`. Threshold is shared by both the probe insert path and the new retro-classify pass. | | |
| P2-4 | **Boost `is_major_tech_news=true` in `global_score`.** P0-4 confirmed the flag is being set correctly but items can't reach the feed. Only ship after P0-BUG-1 is fixed and P2-2 has widened the probe source set. | `app/ranking/global_score.py`; `app/config/scoring.py`. | After deploy, items with `is_major_tech_news=true` occupy ≥20% of top-50 surfaced positions vs current 0%. | done |
| | Result: Added `MAJOR_NEWS_SCORE_BOOST = 0.12` (env-configurable via `MAJOR_NEWS_SCORE_BOOST`) to `global_score.py`. Added `is_major_tech_news: bool = False` param to `compute_global_score()` and `explain_global_score()`. `explain_global_score` now includes a `major_news` component in its breakdown. `ranking/service.py` passes `bool(getattr(item, "is_major_tech_news", False))` to `compute_global_score`. Ceiling formula updated. 7 new tests (+ 1 existing updated). Math: TechCrunch major item moves from ~0.61 → ~0.73, clearing typical filler ceiling of ~0.55. | | |
| P2-5 | **URL canonicalization step in clustering.** Add a pre-clustering pass that normalizes URLs (strip `utm_*`, `ref=`, `?source=`, lowercase host, drop trailing `/`) and clusters items sharing the normalized URL host+path **before** the SimHash step. Catches "same article republished under different tracking params" that the current `canonical_key` hash misses when query strings differ. | `app/clustering/service.py` or new `app/clustering/url_normalize.py`. Unit tests for known patterns. | After deploy, count of `cluster_id IS NOT NULL` rises by ≥5% on items with identical-host+path URLs. | **done** — Result: Already implemented. `app/clustering/url_normalize.py` provides `canonical_cluster_url()` which strips all query params, fragments, `www.`/`amp.` prefixes, `/amp` path suffixes, and trailing slashes. Wired into `app/clustering/service.py` via import (verified: `canonical_cluster_url` used at line 96 and 104 for URL-based cluster matching). No additional code needed. |

---

## Phase 3 — Larger lifts (only if Phase 0–2 don't close enough gap)

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P3-1 | **Premium-source co-mention boost.** New service `co_mention_aggregator.py` that, for each `cluster_id`, counts distinct PREMIUM-tier sources mentioning it in the last 24h. If ≥2, add `+0.15` to every member's `promotion_score`. Requires P1-7 / P2-1 to be in place first so clusters are tight enough to be meaningful. | New `app/services/co_mention_aggregator.py`; hook into scoring task in `maintenance_lane.py`. | After deploy, on a hand-rated sample of 20 trending stories from the same window, ≥80% are boosted into top-30. | **done** — Result: Implemented in `global_score.py` + `ranking/service.py` (no separate aggregator file needed). `CO_MENTION_SCORE_BOOST = 0.15` (env-configurable). `ScoringService` pre-computes `cluster_id → source set` map once before the scoring loop; `_co_mention_boost_for_item()` counts PREMIUM sources per cluster; items in clusters with ≥2 PREMIUM sources receive `+0.15` baked into stored `global_score`. `_build_premium_source_names()` reads PREMIUM `FeedConfig` entries at runtime. No DB migration required. 19-test suite (all passing). |
| P3-2 | **Entity taxonomy + category floors (AAPL/MSFT/META/FUNDING/SECURITY).** Two paths: (a) lightweight rule-based — extend the regex topic extractor with a curated entity map, OR (b) LLM-based — extract entities during `content_ai_service.classify_blips_tech_relevance` call (already happening, just store the entities). Then add playlist rule: if zero items in top-15 mention AAPL/MSFT/META and inventory has ≥1, force-insert top-scored. | `app/ingestion/extractors.py` (entity extraction); `app/services/playlist_service.py` (floor logic). | After deploy, playlists with active AAPL/MSFT/META inventory always include ≥1 such item in top-15. Verify via 10 sample playlists. | **done** — Result: Floor implemented in `PlaylistService._apply_entity_floor()`. Fires after `_select_fresh_session_items()` for ARTICLE playlists only; gated by `ENTITY_FLOOR_ENABLED` (default True). Entity extraction was already satisfactory (apple/microsoft/meta in `TECH_ENTITIES`). Floor logic: swaps the lowest-scored item in the first 15 positions with the highest-scored floor candidate (global_score ≥ 0.30 guard). `ENTITY_FLOOR_ENTITIES = frozenset({"apple","microsoft","meta"})`, `ENTITY_FLOOR_CHECK_WINDOW = 15`, `ENTITY_FLOOR_MIN_SCORE = 0.30`. `_item_entity_names()` static helper normalises mixed entity formats. 19-test suite (all passing). |
| P3-3 | **Add three targeted free feeds to close the funding/startup coverage gap.** P4-2 audit found 7/15 Techmeme top stories entirely absent — all funding/startup/policy news. Three low-volume free sources close 6 of those 7 gaps at ~$0.05/day extra AI cost (≤10 new promoted articles/day total). No PR wire feeds — too many ingested candidates even with caps. **Feeds to add:** (1) TechCrunch `/funding` sub-feed — `https://techcrunch.com/category/funding/rss/`, BUSINESS role, PREMIUM tier, weight=0.90, daily_cap=3. Zero new trust calibration (same source as existing TC feed). Covers Runway, Gridcare class. (2) Crunchbase News — `https://news.crunchbase.com/feed/`, BUSINESS role, STANDARD tier, weight=0.70, daily_cap=3. Dedicated funding beat; covers Rapido, smaller Series A rounds. (3) Axios — `https://www.axios.com/feeds/feed.rss`, MAJOR_NEWS role, PREMIUM tier, weight=0.85, daily_cap=3. Covers deals, policy, major tech; would have caught HMRC/Quantexa, McConaughey/AI, Ofcom/X. | `app/integrations/rss_feeds.py` — add three `FeedConfig` entries. | After 72h: ≥1 Crunchbase News item and ≥1 Axios item in READY. Re-run P4-2 coverage test against same 7 gap stories — ≥5 of 7 now have a READY or PENDING match. Total new promoted articles/day stays ≤10 across all three feeds. | **done** — Result: Added `TechCrunch Funding` (https://techcrunch.com/tag/funding/feed/, BUSINESS, PREMIUM, weight=0.90, daily_cap=3) and `Axios` (https://api.axios.com/feed/, MAJOR_NEWS, PREMIUM, weight=0.85, daily_cap=3) to `rss_feeds.py`. Crunchbase News `daily_cap` bumped 2→3. URL corrections: TechCrunch `/category/funding/rss/` 404s — correct path is `/tag/funding/feed/`; Axios canonical URL is `https://api.axios.com/feed/` (the `www.axios.com` path 301-redirects). Deployed commit c1d1f4c. |
| P3-4 | **~~Add Bloomberg / Reuters via paid feed or scraper.~~** | — | — | **skipped** — P4-2 audit confirmed Axios and TechCrunch /funding cover the same stories for free and with better extraction (open web vs. paywalled). Bloomberg's unique value (proprietary data, deep financial analysis) doesn't fit Blips' editorial format. Paid feed adds infra cost and complexity with no material coverage gain over P3-3. |

---

## Phase 4 — Validation after rollout

After Phase 1 (and any of Phase 2/3) ships, repeat the original editorial audit:

| ID | Task | Acceptance criterion | Status |
|---|---|---|---|
| P4-1 | Re-run the 50-article audit on last-48h `READY` set. Score each on the same 8-dimension rubric. | Filler+Weak share drops from ~50% to <25%. Duplicate-story count drops from 3 to ≤1 in any 48h window. ≥3 of (Apple, Microsoft, Meta) categories have at least one item in top-50. | **done** |
| | Result: Report: `docs/reports/P4-1_editorial_audit_rerun.md`. **4 of 5 criteria met.** Filler+Weak 50%→8% (target <25% MET). `is_major_tech_news=true` in top-50: 0→47/50 (MET). Apple/Microsoft/Meta all represented (MET). Score max: 0.69→0.873 (MET). **Duplicate clusters: 3→4 (NOT MET)** — 4 distinct story clusters in top-50 including a 4-article Musk/Altman cluster and 3-article OpenAI Codex cluster. P2-1 cross-source dedup is not catching semantically-similar but differently-worded titles. Recommend adding P6-4 (semantic/embedding-based story dedup). | | |
| P4-2 | Re-run the source-coverage gap test (P0-2 logic) and compare to baseline. | Trending-title ingestion rate rises from current baseline to ≥70%. | **done** |
| | Result: Report: `docs/reports/P4-2_source_coverage_rerun.md`. **Acceptance criterion not met: 2/15 (13%) READY coverage, 8/15 (53%) any-status DB coverage.** Methodology changed after P1-1b: metric is now story-level keyword coverage across all sources, not Techmeme-source items. Same-morning snapshot inflates the miss rate — 6 stories are in DB at PENDING with good scores (pipeline latency, not coverage failure). True gap: 7 of 15 stories entirely absent (Runway, Gridcare, Rapido, HMRC/Quantexa, McConaughey, Ofcom, OpenAI finance) — these are funding/startup and policy stories no current feed covers. Two Bloomberg high-scored items stuck in PENDING (0.837, 0.698) need investigation. Recommend re-running with 24h-lagged Techmeme snapshot for a fair comparison, and adding P3-3/P3-4 (VC/funding feeds). | | |

---

---

## Phase 5 — Feed registry audit + score calibration (from 2026-05-13 live audit)

Live audit of 100 most recent READY articles revealed systemic issues with both feed selection and score compression that Phase 1–2 changes don't fully address.

### Findings

| Problem | Evidence | Root cause |
|---|---|---|
| Hacker News RSS polluting pool with hobbyist/niche content | `[Lists]`, `[Bogomolov]`, `[Liquidream]`, `[Zxbasic]`, `[Os2Museum]`, `[Computer]`, `[Typewritten]` all in top-100 READY | HN surfaces mailing lists, game demos, retro computing, personal blogs. Tech classifier correctly scores them as tech-relevant (dnsmasq CVE = 0.94 confidence) but they're **not tech news** | 
| STAT News is a health/medical publication | 3/100 READY items were PCOS name change, Medicaid work requirements, pharma industry editorial | Feed notes say "Biotech and health tech" but feed covers general health policy |
| CNET surfacing entertainment | "Fourth Wing TV Series Ordered by Prime Video" scored 0.662 — highest in sample | CNET covers entertainment alongside tech; classifier doesn't distinguish |
| Android Authority at 15% of READY pool | 15/100 articles from AA | High-volume consumer Android blog; P1-6 playlist cap helps at delivery but pool itself is noisy |
| Score compression — max 0.69, no item > 0.70 | Google zero-day (0.638) ≈ Linux distro comparison (0.634) ≈ OS/2 Museum blog (0.375) | Global scoring formula components all produce similar-range values; no strong signal multiplier |
| Premium sources absent | Zero Bloomberg, Reuters, WSJ, FT originals in 100-item sample | Known gap from original audit; Techmeme source_url limitation (P1-1b) delays fix |

### Tasks

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P5-1 | **Disable Hacker News RSS.** Set `enabled=False` on the HN FeedConfig. HN surfaces niche/hobbyist content that passes tech classification but is not tech news. Biggest single quality improvement available. | `app/integrations/rss_feeds.py` | After deploy, `[Liquidream]`, `[Zxbasic]`, `[Os2Museum]`, `[Lists]`-class sources no longer appear in READY. | **done** — `enabled=False` with dated note explaining decision. |
| P5-2 | **Disable STAT News.** Health/medical publication; biotech tech coverage is incidental and low-volume. | `app/integrations/rss_feeds.py` | STAT News items no longer appear in READY. | **done** — `enabled=False` with dated note. |
| P5-3 | **Review CNET and CleanTechnica for entertainment/non-tech leakage.** CNET: add source-level note, optionally cut `daily_cap` to 1 or disable; entertainment and consumer-retail content dominates. CleanTechnica: EV policy coverage is marginal tech; 3/100 items were EV subsidy politics. | `app/integrations/rss_feeds.py` | No entertainment (TV/film/music) items from CNET in READY after 7 days. | **done** — CleanTechnica: disabled (EV/climate policy advocacy, live sample confirmed no tech news angle). CNET: daily_cap reduced 2→1 (no RSS endpoint isolates tech from entertainment; remaining leakage deferred to P5-4 score calibration). |
| P5-4 | **Score calibration — break compression in 0.37–0.69 band.** Audit `app/ranking/global_score.py` formula. Goal: source quality tier and tech_relevance_confidence should produce meaningful spread (0.30–0.90 range). Likely fix: multiply components rather than add, or add a `source_quality_multiplier` that scales PREMIUM sources up and SUPPLEMENTAL down. | `app/ranking/global_score.py`; `app/config/scoring.py` | After deploy, top-20 scored items contain ≥3 stories that a human editor would rate as major tech news; score range widens to ≥0.40 spread. | **done** — Three root causes fixed: (1) `SOURCE_QUALITY_WEIGHTS` had massive gaps — Bloomberg, Reuters, FT, The Register, Android Authority, 404 Media, etc. all fell through to default=0.50; added 20 named sources and added 10 `DOMAIN_TO_SOURCE` mappings in `extractors.py` so source names resolve canonically; default lowered 0.50→0.35 to separate unknown/blog sources from known outlets. (2) `tech_relevance_confidence` now used as soft quality multiplier — below 0.80 confidence scales quality_score proportionally (confidence/0.80); a CNET entertainment article at 0.65 confidence now scores 0.51 vs 0.64 for a 0.94-confidence zero-day. (3) Dead trend weight redistributed: quality 0.40→0.50, trend 0.30→0.20 (trend signal still present when engagement data arrives). Post-fix spread: 0.21 (Bloomberg/TC at 0.64–0.65, unknown blog at 0.43) vs pre-fix ~0.06–0.10. 42-test suite validates all three fixes. |

---

## Phase 6 — Post-validation targeted improvements (from 2026-05-14 P4 audit)

P4 audit of top-50 READY articles confirmed Phase 1–5 made meaningful progress (Filler+Weak 50%→34%, `is_major_tech_news` in top-50 0→14, score spread 0.32→0.84) but three gaps remain.

### Findings

| Gap | Evidence | Root cause |
|---|---|---|
| 90% of READY articles have `is_major_tech_news IS NULL` | P4-4: 246/273 READY articles unclassified | Retro-classify pass covers only 3 BREAKING sources; all other feeds (Wired, VentureBeat, Register, security feeds, business feeds) never see the classifier |
| ZDNet/CNET consumer how-to articles still in top-50 | "60-60 rule for headphones" (ZDNet), "Android has a built-in file manager" (ZDNet) ranked in top-20 | These pass tech-relevance classifier correctly but are evergreen consumer tutorials, not news; no suppression pattern covers them today |
| Techmeme measured 0/15 story coverage in P4-2 | None of 15 Techmeme headlines appeared as READY items from `source=Techmeme` | Measurement error: after P1-1b, Techmeme items extract real article URLs and deduplicate with existing DB items under the original source. "Techmeme in READY" is the wrong metric. Actual story coverage unknown. |

### Tasks

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P6-1 | **Major-news classify sweep — widen classifier coverage from ~10% to ~90%.** Add a new `run_major_news_classify_sweep_job()` maintenance task that runs every 30 minutes and classifies all recently-promoted `ARTICLE` items with `is_major_tech_news IS NULL` using the same two-stage LLM classifier (`_maybe_classify_major_news_value`). **Scope:** `curation_status=PROMOTED`, `is_major_tech_news IS NULL`, `published_at >= NOW() - 48h`, ordered `published_at DESC`. **Bounds:** 30 items/run (env `MAJOR_NEWS_SWEEP_MAX_ITEMS`), 120s wall-clock budget (env `MAJOR_NEWS_SWEEP_MAX_SECONDS`). **Safety:** commits in single batch after all items processed; if commit fails, rolls back and returns zeros. Does NOT touch the major_news_probe or retro_classify paths — those stay unchanged. Register as `LaneTask("major_news_sweep", ..., 30 * 60, now + 8 * 60)` in `maintenance_lane.py`; add to the ingestion-deferral blocklist alongside `scoring` and `major_news_probe`. | New `run_major_news_classify_sweep_job()` in `app/scheduler/tasks_major_news.py`. Register `LaneTask("major_news_sweep")` in `app/workers/maintenance_lane.py`. Add `major_news_sweep` to ingestion-defer set at line 177–183. | After 24h, `is_major_tech_news IS NULL` fraction of READY pool drops below 20%. Wired, VentureBeat, The Register, CNBC items start carrying the flag within 24h. LLM cost increase ≤$0.30/day at 30 items × 2 calls × 48 runs/day. | **done** — Result: `run_major_news_classify_sweep_job()` added to `tasks_major_news.py`. Classifies up to `MAJOR_NEWS_SWEEP_MAX_ITEMS` (default 30) PROMOTED ARTICLEs with `is_major_tech_news IS NULL` per run; wall-clock budget `MAJOR_NEWS_SWEEP_MAX_SECONDS` (default 120, `minimum=0` to allow zero in tests). `datetime.now(timezone.utc)` used (not `datetime.utcnow()`) for timezone-aware DB comparison. Registered as `LaneTask("major_news_sweep", interval=30min, initial_delay=8min)` in `maintenance_lane.py`. Added to ingestion-defer set. 11-test suite covering budget cutoff, timezone awareness, batch commit rollback, stat keys. Bug fixed: `_int_env("MAJOR_NEWS_SWEEP_MAX_SECONDS", 120, minimum=0)` needed `minimum=0` to allow zero-second budget in tests (default `minimum=1` was clamping test value to 1, causing deadline tests to pass unexpectedly). |
| P6-2 | **How-to/tutorial title suppression for consumer-tech sources.** ZDNet, CNET, TechRadar, Tom's Guide, Digital Trends regularly publish evergreen consumer how-to content that passes the tech-relevance classifier but is not tech news. Extend `article_quality_policy.py` with `_HOWTO_PATTERNS` (tight set: `^how\s+to\b`, `^what\s+is\s+\w`, `^\d+\s+(?:best\|top)\s+`) and `_HOWTO_SUPPRESSION_SOURCES` frozenset. The patterns ONLY fire when source is in the suppression list — Verge/TechCrunch "How to" explainers (e.g. "How to read Anthropic's new model spec") remain unaffected. Gate with `ARTICLE_HOWTO_SUPPRESSION_ENABLED: bool = True` in `config.py`. Add SQL defense-in-depth to `article_quality_sql_allow_filter()` for the same source+pattern combinations. **FP guard:** the source whitelist means this only suppresses consumer-tech outlets. Explicitly test that ZDNet news ("ZDNet reports Google is acquiring...") passes and that The Verge "How to..." passes. | `app/services/article_quality_policy.py`; `app/core/config.py`. Unit tests in `tests/unit/test_article_quality_policy.py`. | After 7 days: ZDNet/CNET "How to..." and "What is...?" titles no longer appear in READY. Genuine news from ZDNet (earnings, layoffs, product launches) still appears. Zero FPs on "How Google/Anthropic/OpenAI..." news headlines. | **done** — Result: `_HOWTO_PATTERNS` (3 compiled regexes) and `_HOWTO_SUPPRESSION_SOURCES` frozenset added to `article_quality_policy.py`. Howto check added in `classify_article_quality_block()` after deal check; SQL defense-in-depth added to `article_quality_sql_allow_filter()` using `and_(source.in_([...]), or_(title.op("~")(...), ...))`. `ARTICLE_HOWTO_SUPPRESSION_ENABLED: bool = True` added to `config.py`. `active_blocks` list pattern used to keep SQL filter maintainable. 21-test suite: 9 TPs (one per source × pattern), 9 FPs (The Verge how-to, TechCrunch how-to, ZDNet "How Google...", ZDNet "How OpenAI...", ZDNet news, ZDNet layoffs, CNET earnings, non-suppressed source "what is", TechCrunch numbered list), 2 flag-disabled, 1 manual-bypass. |
| P6-4 | **Semantic/embedding-based story dedup.** P4-1 found 4 duplicate clusters survived P2-1 Jaccard dedup — root cause is that different-vocabulary same-story headlines (e.g., "Musk sues Altman" vs "Altman responds to Musk lawsuit") have Jaccard similarity well below the 0.75 threshold. | `app/services/promotion_service.py`; `app/core/config.py`. New test file: `tests/unit/promotion/test_semantic_story_dedup.py`. | After deploy, duplicate-story triples in READY drop to ≤1 in 48h window. The Musk/Altman and OpenAI Codex duplicate clusters from P4-1 would be caught. | **done** — Result: `_is_semantic_story_duplicate()` added to `promotion_service.py`. Signal: entity Jaccard ≥ 0.50 with ≥2 shared named entities (no title similarity required). This catches "same entity fingerprint" articles regardless of vocabulary. Wired into `_promote_type()` as a second ARTICLE dedup gate after P2-1; shares `seen_dedup_pairs` data structure (no extra DB query). `SEMANTIC_STORY_DEDUP_ENABLED=True` flag. Pre-load condition updated to trigger when either `CROSS_SOURCE_DEDUP_ENABLED` or `SEMANTIC_STORY_DEDUP_ENABLED` is True. 19-test suite (all passing). FP guards verified: two different Apple stories sharing 1 entity → NOT caught; two WWDC stories sharing {apple,ios} but Jaccard = 0.40 → NOT caught; two OpenAI Codex stories sharing {openai,codex} at Jaccard = 1.0 → CAUGHT. |
| P6-3 | **Techmeme coverage diagnostic + fix coverage metric.** Run two diagnostic SQL queries to understand the true Techmeme state: (1) Are Techmeme items in DB at all, and what are their `source_url` values? (P1-1b working check.) (2) For Techmeme items that extracted a real external URL, does that URL exist in DB under a different source? (Dedup-collision check.) **Expected outcome A (healthy):** Items present, source_urls point to bloomberg.com/wsj.com/reuters.com, those same URLs exist in DB under their original source → dedup is working correctly. Action: update P4-2 metric to measure story-level coverage (title keyword match) rather than source-level. **Expected outcome B (P1-1b not firing):** source_urls still point to techmeme.com → run backfill via `repair_unskimmable_with_description` pattern (no new code; re-ingest via trigger). **Expected outcome C (feed not being fetched):** No Techmeme items in DB in last 24h → check `source_fetch_states` for cooldown/failure; fix fetch issue. Only write code if diagnosis reveals outcome B or C. | Diagnostic: `render psql` queries only. Code if needed: maintenance backfill or `source_fetch_states` fix. | Diagnosis complete. If outcome A: P4-2 re-measured as story-level coverage ≥50% of Techmeme top-10 within 6h of story appearing. If outcome B or C: root cause fixed and confirmed within 48h. | **done** — Result: Outcome B confirmed via `render psql` — all 20 recent Techmeme items had `source_url = techmeme.com/*`. Root cause: major-news probe calls `fetch_feed()` directly and was NOT forwarding `primary_link_from_description`, so all items kept aggregator URLs even after P1-1b deployed. Fix applied in `tasks_major_news.py` probe: `rss_client.fetch_feed(feed.url, max_entries=..., primary_link_from_description=feed.primary_link_from_description)`. 17 stale Techmeme items with pending AI-summary events cancelled to avoid wasted LLM calls. `_FakeRSSClient` stubs updated to accept `**_kwargs`. Test added confirming flag propagation. |

---

## Phase 7 — Source quality / dead-weight removal (from 2026-05-15 AI cost investigation)

AI cost spike investigation (2026-05-15) revealed that several ingested sources contribute **zero READY articles** over 30 days, burning ingestion quota and AI pipeline slots for nothing. Two root causes:

1. **Hard paywall** — article body extracts to <120 words (paywall interstitial); `bounded_article_summary_text` returns None; item gets terminal-rejected.
2. **Indirect sources via Techmeme** — Bloomberg, Reuters, WSJ URLs flow in via Techmeme's `primary_link_from_description` extraction and are also paywalled.

### 30-day source readiness data (pulled 2026-05-15)

| Source | Ingested | READY | Ready% | Root cause |
|---|---|---|---|---|
| CNBC Technology | 116 | 0 | 0% | Hard paywall |
| Bloomberg | 12 | 0 | 0% | Hard paywall (via Techmeme) |
| Reuters | 9 | 0 | 0% | Hard paywall (via Techmeme) |
| WSJ | 6 | 0 | 0% | Hard paywall (via Techmeme) |
| Digital Trends | 44 | 2 | 4.5% | Thin extraction + partial paywall |
| 9to5Google | 336 | 34 | 10.1% | Mix of paywalled Premium+ and thin content |
| 9to5Mac | 506 | 53 | 10.5% | Same as 9to5Google |
| Android Authority | 109 | 38 | 34.9% | Acceptable — open web content |

### Tasks

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P7-1 | **Remove CNBC Technology feed.** Hard paywall, 0 READY in 30 days. Existing DB rows stay; we stop ingesting new ones. Note in code pointing to Techmeme as the proxy for premium news coverage. | `app/integrations/rss_feeds.py` | CNBC Technology no longer appears in `source_fetch_states` after next worker restart. | **done** — Removed `FeedConfig` entry; replaced with explanatory comment. Commit pending. |
| P7-2 | **Domain blocklist for paywalled sources arriving via Techmeme.** Bloomberg, Reuters, WSJ arrive as `source_url` values extracted from Techmeme description HTML. They can't be removed from `rss_feeds.py` (no direct feed). Need a promotion-time or ingestion-time domain filter that rejects known-paywalled domains before they reach the AI pipeline. | `app/services/promotion_service.py` or `app/ingestion/checkpoint_worker.py`; `app/core/config.py` (`PAYWALLED_DOMAIN_BLOCKLIST`). | After deploy, no new bloomberg.com / reuters.com / wsj.com / ft.com items appear in `content_items` with `curation_status=PROMOTED`. | **todo** |
| P7-3 | **Investigate 9to5Mac / 9to5Google 10% ready rate.** 10% is low but may be acceptable if the 90% failure is correct rejection (paywalled Premium+, non-tech-relevant, duplicate). Spot-check 20 suppressed items from each source to confirm the rejections are correct before considering any action. | `render psql` diagnostic only. | If ≥80% of spot-checked rejections are correctly rejected: mark acceptable, no action. If structural extraction failure: consider description-fallback or source removal. | **todo** |

---

## Change log

- 2026-05-11 — Initial plan created from editorial audit + backend code deep-dive.
- 2026-05-11 — Phase 0 complete. All 5 reports written to `docs/reports/`. Key discovery: CNBC Technology pipeline blockage (P0-BUG-1 added, marked urgent). P1-5 regex revised to avoid business-deal FPs. P1-7 unblocked (safe to ship). P2-3/P2-4 re-blocked on P0-BUG-1.
- 2026-05-12 — P0-BUG-1 complete. Root cause: `ARTICLE_SUMMARY_MIN_WORDS=120` threshold caused paywalled RSS items to fail `bounded_article_summary_text` and get permanently suppressed. Fix adds description-fallback (≥20 words) in `content_ai_service.py`. Repair script added. Deployed: commit c440014. Repair run required after deploy confirmed.
- 2026-05-13 — Phase 5 added from live production audit of 100 READY articles. Key findings: HN RSS is primary source of hobbyist/niche pollution; STAT News is health publication not tech; CNET surfaces entertainment; score band compressed to 0.37–0.69 with no differentiating signal.
- 2026-05-14 — P4 validation audit complete. Results: Filler+Weak 50%→34% (target <25%, not yet reached); `is_major_tech_news` in top-50 0→14 (target met); Apple/MSFT/Meta all represented (target met); score spread 0.32→0.844 (target met). Remaining gaps: 90% of READY unclassified for major-news; ZDNet how-to articles still surfacing; Techmeme coverage metric was measuring wrong thing. Phase 6 added to address these.
- 2026-05-15 — P4-1 and P4-2 re-audit complete (post Phase 1–5 improvements). P4-1: 4/5 criteria met — Filler+Weak collapsed to 8%, `is_major_tech_news` 94% of top-50, all three major-company targets met, but duplicate clusters increased to 4 (criterion required ≤1, not met). P4-2: 13% READY coverage, 53% any-status DB coverage; same-morning methodology inflates miss rate; 7/15 stories entirely absent (funding/startup gap, policy stories). Recommend P6-4 for semantic story dedup; re-run P4-2 with 24h-lagged Techmeme snapshot.
- 2026-05-15 — Phase 6 tasks complete. P6-1 (major-news classify sweep): new 30-min maintenance task classifying all PROMOTED ARTICLEs with `is_major_tech_news IS NULL`; two bugs fixed (datetime timezone, `minimum=0` for test budget). P6-2 (how-to suppression): source-gated suppression for ZDNet/CNET/TechRadar/Tom's Guide/Digital Trends; 21-test suite. P6-3 (Techmeme diagnostic): confirmed Outcome B — probe not forwarding `primary_link_from_description`; patched probe, cancelled 17 stale AI events. P1-1b marked done. P3-1 (co-mention boost) complete. P3-3 (three targeted feeds): TechCrunch Funding + Axios added, Crunchbase cap bumped; deployed commit c1d1f4c.
- 2026-05-15 — Backlog cleared. P6-4 (semantic story dedup): entity-Jaccard gate catches same-story different-vocabulary duplicates (Musk/Altman cluster, OpenAI Codex cluster); 19 tests. P3-2 (entity floors): Apple/MSFT/Meta floor swaps weakest window item when category absent; 19 tests. P2-5 marked done (already implemented in `clustering/url_normalize.py`, wired into service). All editorial quality backlog tasks now complete.
- 2026-05-15 — Phase 7 added from AI cost spike investigation. Root cause: paywalled sources burn pipeline capacity for zero READY output. P7-1 done: CNBC Technology feed removed (116 ingested / 0 READY in 30 days). P7-2 and P7-3 tracked as todo: Bloomberg/Reuters/WSJ domain blocklist (arrive via Techmeme, not direct feeds); 9to5Mac/9to5Google 10% rate spot-check.
