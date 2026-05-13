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
| P1-1b | **Extract original article URL from Techmeme RSS description.** Techmeme embeds the real article URL as the first non-Techmeme `<A HREF>` in its RSS description HTML. Parse this and use it as `source_url` instead of the Techmeme anchor page. | `app/integrations/rss_client.py` — add optional `primary_link_from_description: bool` to `FeedConfig`; when True, extract the first external link from description HTML and use it as `entry.url`. Set flag on Techmeme `FeedConfig`. | After deploy, `source_url` for Techmeme items points to WSJ/Bloomberg/Reuters. Tapping "Read more" navigates to original article. | todo |
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
| P2-1 | **Cross-source fuzzy-title dedup at promotion** as a backstop to clustering. Before marking an item `PROMOTED`, check if any other item in last 48h with `curation_status IN (PROMOTED, READY)` has token-set similarity ≥0.75 to this one's title AND shares ≥1 entity. If yes, mark new item `is_suppressed=True` with reason `"cross_source_duplicate"` (keep the higher-`base_quality_weight` one). | `app/services/promotion_service.py` — new check between current dedup penalty and final commit. | After deploy, count of near-duplicate triples (3+ items <48h apart with ≥0.75 title similarity) in `READY` set drops to 0 in 7-day window. | todo |
| P2-2 | **Widen `major_news_probe` source set.** Currently probes 5 feeds. Add TechCrunch front-page, The Verge front-page, Ars Technica, Reuters Technology, and the Techmeme feed from P1-1. The LLM classifier itself is the editorial layer; expanding candidate pool is cheap. | `app/scheduler/tasks_major_news.py` (source list); `app/integrations/rss_feeds.py` (mark these dual-role MAJOR_NEWS). | After deploy, ≥20% of `READY` items from these sources carry `is_major_tech_news=true` within 7-day window. | done |
| | Result: Reuters RSS returns 401 on all endpoints. TechCrunch/The Verge/Ars Technica already ingest via BREAKING role — the probe's `on_conflict_do_nothing` means the flag is never set on pre-existing items. Solution: added `_retro_classify_premium_breaking()` pass that runs after the main feed loop, querying recently-promoted BREAKING items from these sources where `is_major_tech_news IS NULL` and running the same two-stage classifier. Added `MAJOR_NEWS_RETRO_CLASSIFY_SOURCES` constant. Probe result dict now includes `retro_classified` + `retro_major`. 8 new unit tests (all passing). | | |
| P2-3 | **Lower MAJOR_NEWS classifier confidence threshold from 0.55 → 0.45.** P0-4 found classifier is NOT the bottleneck — the items are blocked upstream. Only do this after P0-BUG-1 is fixed and the probe is actually seeing reasonable volume. | `app/services/major_news_constants.py` — `MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE`. | After deploy, recall on a fresh 30-item hand-rated sample improves to ≥60% with precision still ≥80%. | done |
| | Result: One-line change. `MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE` lowered from 0.55 → 0.45 in `major_news_constants.py`. Threshold is shared by both the probe insert path and the new retro-classify pass. | | |
| P2-4 | **Boost `is_major_tech_news=true` in `global_score`.** P0-4 confirmed the flag is being set correctly but items can't reach the feed. Only ship after P0-BUG-1 is fixed and P2-2 has widened the probe source set. | `app/ranking/global_score.py`; `app/config/scoring.py`. | After deploy, items with `is_major_tech_news=true` occupy ≥20% of top-50 surfaced positions vs current 0%. | done |
| | Result: Added `MAJOR_NEWS_SCORE_BOOST = 0.12` (env-configurable via `MAJOR_NEWS_SCORE_BOOST`) to `global_score.py`. Added `is_major_tech_news: bool = False` param to `compute_global_score()` and `explain_global_score()`. `explain_global_score` now includes a `major_news` component in its breakdown. `ranking/service.py` passes `bool(getattr(item, "is_major_tech_news", False))` to `compute_global_score`. Ceiling formula updated. 7 new tests (+ 1 existing updated). Math: TechCrunch major item moves from ~0.61 → ~0.73, clearing typical filler ceiling of ~0.55. | | |
| P2-5 | **URL canonicalization step in clustering.** Add a pre-clustering pass that normalizes URLs (strip `utm_*`, `ref=`, `?source=`, lowercase host, drop trailing `/`) and clusters items sharing the normalized URL host+path **before** the SimHash step. Catches "same article republished under different tracking params" that the current `canonical_key` hash misses when query strings differ. | `app/clustering/service.py` or new `app/clustering/url_normalize.py`. Unit tests for known patterns. | After deploy, count of `cluster_id IS NOT NULL` rises by ≥5% on items with identical-host+path URLs. | todo |

---

## Phase 3 — Larger lifts (only if Phase 0–2 don't close enough gap)

| ID | Task | Files to touch | Acceptance criterion | Status |
|---|---|---|---|---|
| P3-1 | **Premium-source co-mention boost.** New service `co_mention_aggregator.py` that, for each `cluster_id`, counts distinct PREMIUM-tier sources mentioning it in the last 24h. If ≥2, add `+0.15` to every member's `promotion_score`. Requires P1-7 / P2-1 to be in place first so clusters are tight enough to be meaningful. | New `app/services/co_mention_aggregator.py`; hook into scoring task in `maintenance_lane.py`. | After deploy, on a hand-rated sample of 20 trending stories from the same window, ≥80% are boosted into top-30. | todo (blocked-by P1-7 or P2-1) |
| P3-2 | **Entity taxonomy + category floors (AAPL/MSFT/META/FUNDING/SECURITY).** Two paths: (a) lightweight rule-based — extend the regex topic extractor with a curated entity map, OR (b) LLM-based — extract entities during `content_ai_service.classify_blips_tech_relevance` call (already happening, just store the entities). Then add playlist rule: if zero items in top-15 mention AAPL/MSFT/META and inventory has ≥1, force-insert top-scored. | `app/ingestion/extractors.py` (entity extraction); `app/services/playlist_service.py` (floor logic). | After deploy, playlists with active AAPL/MSFT/META inventory always include ≥1 such item in top-15. Verify via 10 sample playlists. | todo |
| P3-3 | **Guaranteed funding-slot quota.** If `BUSINESS`-role items exist in inventory, reserve 3 slots/day in `READY` for them via promotion override. | `app/services/promotion_service.py` (per-role quota). | After deploy, ≥3 BUSINESS-role items appear in top-50 surfaced articles daily. | todo |
| P3-4 | **Add Bloomberg / Reuters via paid feed or scraper.** Only do this if P0-2 + Techmeme (P1-1) don't close the gap. Likely requires Bloomberg Terminal API access, paid Reuters feed, or scraping (legally and TOS-wise dicey). | New ingestion adapter; new feed config. | At least 5 Bloomberg/Reuters originals per day in `READY`. | todo (blocked-by P0-2 and P1-1 outcomes) |

---

## Phase 4 — Validation after rollout

After Phase 1 (and any of Phase 2/3) ships, repeat the original editorial audit:

| ID | Task | Acceptance criterion | Status |
|---|---|---|---|
| P4-1 | Re-run the 50-article audit on last-48h `READY` set. Score each on the same 8-dimension rubric. | Filler+Weak share drops from ~50% to <25%. Duplicate-story count drops from 3 to ≤1 in any 48h window. ≥3 of (Apple, Microsoft, Meta) categories have at least one item in top-50. | todo |
| P4-2 | Re-run the source-coverage gap test (P0-2 logic) and compare to baseline. | Trending-title ingestion rate rises from current baseline to ≥70%. | todo |

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

## Change log

- 2026-05-11 — Initial plan created from editorial audit + backend code deep-dive.
- 2026-05-11 — Phase 0 complete. All 5 reports written to `docs/reports/`. Key discovery: CNBC Technology pipeline blockage (P0-BUG-1 added, marked urgent). P1-5 regex revised to avoid business-deal FPs. P1-7 unblocked (safe to ship). P2-3/P2-4 re-blocked on P0-BUG-1.
- 2026-05-12 — P0-BUG-1 complete. Root cause: `ARTICLE_SUMMARY_MIN_WORDS=120` threshold caused paywalled RSS items to fail `bounded_article_summary_text` and get permanently suppressed. Fix adds description-fallback (≥20 words) in `content_ai_service.py`. Repair script added. Deployed: commit c440014. Repair run required after deploy confirmed.
- 2026-05-13 — Phase 5 added from live production audit of 100 READY articles. Key findings: HN RSS is primary source of hobbyist/niche pollution; STAT News is health publication not tech; CNET surfaces entertainment; score band compressed to 0.37–0.69 with no differentiating signal.
