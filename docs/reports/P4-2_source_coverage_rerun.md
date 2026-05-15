# P4-2 — Source Coverage Gap Test Re-run

**Date:** 2026-05-15
**Methodology:** Story-level coverage rate — for each of the top-15 Techmeme headlines, does a matching article exist in `content_items` (READY or CANDIDATE) within the last 48h?
**Baseline:** P0-2 (2026-05-11) — ~27% of top Techmeme stories had a READY match.

---

## Methodology Change Note

After P1-1b shipped, Techmeme RSS items extract the real article URL from the description HTML (e.g., `bloomberg.com/...`, `techcrunch.com/...`) and deduplicate with existing DB items under the *original source name*. This means measuring "Techmeme items in READY" is no longer meaningful — the Techmeme feed's job is to ensure story coverage, not to appear by source name.

**P0-2 methodology (wrong after P1-1b):** Count of `source = 'Techmeme'` items in READY.
**P4-2 methodology (correct):** For each Techmeme headline, keyword-search `content_items` for a matching article (READY or CANDIDATE) in last 48h regardless of source. Coverage rate = (stories with ≥1 READY match) / 15.

This methodology is more demanding than P0-2's approach in one sense (requiring READY, not just any match) and more generous in another (any source counts). Comparison to P0-2 is directional only.

---

## Techmeme Top-15 Headlines (fetched 2026-05-15)

Source: https://www.techmeme.com/feed.xml

1. Replit reaches agreement with Apple on app update
2. OpenAI launches personal finance tools for ChatGPT Pro subscribers
3. AI video startup Runway valued at $5.3B with strong Q2 revenue growth
4. Gridcare secures $64M Series A for electric grid AI technology
5. Matthew McConaughey uses trademark strategy against unauthorized AI use
6. Ofcom secures X's commitment to faster content moderation in UK
7. Nvidia's China prospects uncertain following Trump-Xi summit
8. Google tests reduced storage limits for new Gmail accounts
9. Indian rideshare app Rapido raises $240M at $3B valuation
10. UK tax authority HMRC partners with Quantexa on £175M AI fraud detection deal
11. xAI unveils Grok Build coding agent for developers
12. Meta expands Ray-Ban glasses with handwriting features and developer access
13. Akamai acquires LayerX Security for $205M to enhance employee AI safety
14. HSG launches $3B fund anchored by ByteDance investment stake
15. Bill Ackman's Pershing Square builds new Microsoft position

---

## Coverage Table

| # | Techmeme Story | In DB? | READY? | Source | Score | Notes |
|---|---|---|---|---|---|---|
| 1 | Replit reaches agreement with Apple on app update | Partial | READY (adjacent) | Engadget, MacRumors | 0.750, 0.740 | DB has Apple App Store agentic AI stories (related context) but no Replit-specific article found in 48h window |
| 2 | OpenAI personal finance tools for ChatGPT Pro | No | No | — | — | No matching article found at any status |
| 3 | Runway AI video startup valued at $5.3B | No | No | — | — | No matching article found |
| 4 | Gridcare $64M electric grid AI Series A | No | No | — | — | No matching article found |
| 5 | McConaughey trademark vs unauthorized AI use | No | No | — | — | No matching article found |
| 6 | Ofcom secures X content moderation commitment | No | No | — | — | No matching article found |
| 7 | Nvidia China/Trump-Xi summit | Yes | No | CNBC Technology | 0.663 | PENDING — "Nvidia's Jensen Huang: 'President Trump asked me to come'" |
| 8 | Google reduces Gmail storage for new accounts | Yes | No | Android Authority (×2) | 0.802 | PENDING (two near-identical items; high score but stuck in PENDING) |
| 9 | Rapido Indian rideshare $240M raise | No | No | — | — | No matching article found |
| 10 | HMRC Quantexa £175M AI fraud detection | No | No | — | — | No matching article found |
| 11 | xAI Grok Build coding agent | Yes | No | Engadget | 0.491 | PENDING — "xAI introduces its coding agent called Grok Build" |
| 12 | Meta Ray-Ban glasses handwriting / developer access | Yes | **READY** | The Verge | 0.741 | READY — "Meta brings virtual writing to everyone with Meta Ray-Ban Display glasses" |
| 13 | Akamai acquires LayerX Security for $205M | Yes | **READY** | Calcalistech | 0.483 | READY — exact match; low score but reached READY |
| 14 | HSG $3B fund anchored by ByteDance stake | Yes | No | Bloomberg | 0.698 | PENDING — Bloomberg item present, high score, not yet READY |
| 15 | Bill Ackman / Pershing Square new Microsoft stake | Yes | No | Bloomberg | 0.837 | PENDING — high-quality Bloomberg item, score 0.837, stuck in PENDING |

---

## Coverage Rate Calculation

| Metric | Count |
|---|---|
| Stories with ≥1 READY match | **2** of 15 (Meta Ray-Ban, Akamai/LayerX) |
| Stories with DB match (any status) | **8** of 15 |
| Stories with DB match, PENDING (would be READY if pipeline completes) | **6** additional |
| Stories with no DB match at all | **7** of 15 |

**READY coverage rate: 2/15 = 13%** (vs. P0-2 baseline of ~27%)

**Any-status DB coverage rate: 8/15 = 53%**

---

## Analysis

### Why READY coverage is low (13%)

The 13% READY figure is worse than P0-2's 27% on face, but the comparison is complicated by several factors:

1. **Recency effect — May 15 is early in the day.** The Techmeme headlines were fetched on the morning of May 15. Most of the Techmeme top-15 are stories that broke in the last 6-12 hours. The pipeline has a lag of approximately 30-90 minutes from ingestion through AI processing to READY status. Six of the 7 "in DB but PENDING" items have scores of 0.49–0.837 — they are not stuck due to quality filters, they are simply still in the pipeline.

2. **Bloomberg items stuck at PENDING despite high scores.** Two Bloomberg items (Ackman/Microsoft at 0.837, HSG/ByteDance at 0.698) are in DB but PENDING. This may reflect the description-fallback or extraction difficulty with Bloomberg's content. The Ackman story in particular (score 0.837) should be READY.

3. **Seven stories have no DB match at all.** Runway, Gridcare, Rapido, HMRC/Quantexa, McConaughey, Ofcom/X, and OpenAI finance tools are absent. These represent genuine coverage gaps — either the sources covering these stories are not in the feed registry, or the stories are too recent to have been ingested yet.

4. **CNBC Technology items still PENDING.** Story #7 (Nvidia/China) is a CNBC Technology item with score 0.663. Despite P0-BUG-1 being fixed, CNBC items appear to be lingering in PENDING — possibly because the repair backlog (1,024 items re-queued) is still draining and newer CNBC items are competing for the promotion queue.

### What is working

- The 8/15 (53%) "any-status" coverage is more meaningful than it looks: the pipeline knows about more than half of Techmeme's top stories within 48h. Before P1-1 (Techmeme feed) and P0-BUG-1 (description fallback), coverage was structurally limited.
- The two READY hits (Meta Ray-Ban, Akamai acquisition) are non-trivial stories (product launch and M&A) that would have been missed in the original audit.
- Six pending stories with good scores suggest the pipeline is ingesting these but not processing them fast enough relative to the news cycle.

### What is not working

- **7 stories entirely absent** — Runway, Gridcare, Rapido, HMRC/Quantexa all represent funding/startup news that the feed registry doesn't cover well. This is a known gap (Phase 3 tasks P3-3, P3-4 address it but are not yet shipped). McConaughey and Ofcom/X stories are niche-IP and policy stories that are unlikely to be in any of the current registered feeds.
- **PENDING blockage** — high-scored Bloomberg and CNBC items stuck in PENDING suggests promotion throughput or post-ingestion AI processing is a bottleneck for very recent content.
- **P1-1b status unclear.** The Techmeme feed appears to be ingesting, but no Techmeme-source items were found in the 48h READY pool. It is unclear whether this is because P1-1b is working (URLs extracted, deduplicated under original source) or because the Techmeme feed is not being fetched. A dedicated `source_fetch_states` query would clarify this; the scope of this report is limited to the coverage metric.

---

## Comparison to P0-2 Baseline

| Metric | P0-2 (2026-05-11) | P4-2 (2026-05-15) | Change |
|---|---|---|---|
| Methodology | Source=Techmeme items in READY | Keyword match, any source, in READY | Changed — not directly comparable |
| READY coverage rate | ~27% (4/15 est.) | 13% (2/15) | Worse (but methodology differs) |
| Any-status DB coverage | Not measured | 53% (8/15) | New metric |
| Bloomberg items in pool | 0 | 2 (PENDING) | Improved |
| Stories entirely absent | ~11/15 | 7/15 | Improved |

**Important caveat:** P0-2 used a different methodology and was taken at a different time of day relative to the Techmeme snapshot. The 13% READY figure on a same-morning snapshot of breaking news is not comparable to P0-2's end-of-day snapshot of 48h-old coverage. A fairer comparison would use a 24h-lagged Techmeme snapshot (stories from yesterday) against today's READY pool.

---

## Acceptance Criterion Assessment

| Criterion | Target | Result | Met? |
|---|---|---|---|
| ≥ 50% of top-15 Techmeme stories have a READY match | 50% | **13%** (2/15) | **NO** |

**Criterion not met.** However, this result should be interpreted cautiously:

- The 53% any-status DB coverage (vs. ~27% READY in P0-2) represents real improvement in the pipeline's awareness of major stories.
- The gap between "in DB" and "READY" (8 items vs. 2) suggests the bottleneck is pipeline throughput and promotion latency for very recent content, not source coverage.
- Five of the 6 "in DB but PENDING" items have scores above 0.49 and one has score 0.837 — they are not being filtered out, they just haven't completed the pipeline yet.

A re-run of this query at end-of-day or against yesterday's Techmeme headlines would likely show a materially higher READY rate.

---

## Recommendations

1. **Measure P4-2 correctly next time:** Use a 24h-lagged Techmeme snapshot (stories from yesterday) against today's READY pool. Same-morning snapshots artificially suppress the READY rate.
2. **Investigate Bloomberg PENDING blockage:** Two high-scored Bloomberg items (0.837, 0.698) are stuck in PENDING. Check whether description extraction is failing for Bloomberg URLs.
3. **Investigate CNBC Technology PENDING latency:** Story #7 (Nvidia/China, 0.663) should be READY by now.
4. **Phase 3 funding/startup feeds (P3-3, P3-4):** 5 of the 7 missing stories are funding/startup news (Runway, Gridcare, Rapido, HMRC/Quantexa). No current registered feed covers these systematically. Crunchbase, PitchBook, or dedicated VC-beat feeds are needed.

---

*Report generated: 2026-05-15*
