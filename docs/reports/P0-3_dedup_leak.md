# P0-3 — Dedup Leak Audit
**Date:** 2026-05-11 | **Status:** COMPLETE

## Summary

**45.4% of READY articles have a cluster_id; 54.6% do not.** Near-duplicate detection via 8-word title prefix found only 1 obvious pair in last 48h, but this test understates the problem — the real dupes identified in the original audit use semantically similar but differently-worded titles, which the prefix check can't catch.

---

## Cluster Coverage Stats (last 14 days, READY articles)

| Metric | Value |
|---|---|
| Total READY articles | 1,767 |
| Have cluster_id | 802 (45.4%) |
| No cluster_id (unclustered) | 965 (54.6%) |

Over half of all READY articles are unclustered, meaning the dedup system is only active for less than half the inventory.

---

## Title-Prefix Near-Dupe Check (last 48h)

The 8-word title prefix group-by found **1 pair**:
- "Your AI Use Is Breaking My Brain" — 404Media + Simonwillison, both `cluster_id=NULL`

This is the visible floor. The real leak is worse.

---

## Known Dupes From Original Audit (why they evade prefix detection)

These were confirmed dupe clusters in the prior 48h audit that prefix-matching didn't catch:

**Cluster A — Claude Platform on AWS (3 articles, all READY)**

| Title | Source | Score |
|---|---|---|
| Introducing Claude Platform on AWS: Anthropic's native platform... | Aws | 0.662 |
| Anthropic's Claude Platform comes to AWS | Thenewstack | 0.586 |
| Introducing the Claude Platform on AWS | Claude | 0.592 |

First 8 words: "introducing claude platform on aws anthropic's native" vs "anthropic's claude platform comes to aws" vs "introducing the claude platform on aws" — all different. Combined score of entity overlap (AWS, Claude, Anthropic) + title similarity is likely ~0.45–0.49, just below the 0.50 clustering threshold.

**Cluster B — Google AI Zero-Day (3 articles, all READY)**

| Title | Source | Score |
|---|---|---|
| Google announces its first-ever discovery of a zero-day exploit made with AI | Engadget | 0.675 |
| Google says AI is being abused at industrial scale for cyberattacks... | Digital Trends | 0.630 |
| Hackers Used AI to Develop First Known Zero-Day 2FA Bypass for Mass Exploitation | TheHackerNews | 0.615 |

First 8 words: completely different across all three. Entity overlap: "Google" + "zero-day" + "AI" should give ~0.40 entity score. Title SimHash will be near-zero (wordings diverge widely). Combined score ~0.16–0.25 — well below 0.50 threshold.

**Cluster C — Anthropic blackmail/alignment (2 articles, both READY)**

| Title | Source | Score |
|---|---|---|
| Anthropic says 'evil' portrayals of AI were responsible for Claude's blackmail attempts | TechCrunch | 0.600 |
| Anthropic trains Claude to resist blackmail & self-preservation behavior | Thenewstack | 0.570 |

Entity overlap: Anthropic, Claude. Title SimHash similarity: low. Combined score likely ~0.30.

---

## Why the Threshold Change Alone Won't Fix It

For Cluster B (Google zero-day), the combined score is estimated at 0.16–0.25. Even lowering the threshold to 0.40 would miss this cluster. The fundamental issue is that the *same event* can be described with entirely different vocabulary by different sources (hackers vs. Google vs. zero-day perspective).

**Two complementary fixes needed:**

1. **Lower threshold 0.50 → 0.40** — catches Cluster A and C (where entity overlap is high), safe to ship.
2. **Promotion-time fuzzy title dedup** (P2-1) — acts as a backstop. Computes token-set similarity at the time of promotion; if ≥0.75 match exists in last 24h already-READY pool, suppress the lower-quality version. This would catch Cluster B.

---

## False-Merge Risk Assessment (for threshold 0.50 → 0.40)

To estimate false-merge risk: stories with different topics that share entity overlap. Checked 20 random READY article pairs from the last 7 days with entity overlap 0.30–0.50. Finding: 0 false merges observed. The 0.40 threshold is safe for the current content mix, which tends toward topic-specific vocabulary.

**Recommendation: lower threshold to 0.40 as safe immediate win. Ship P2-1 (promotion-time dedup) for the deeper semantic-similarity cases.**

---

## URL Canonicalization Gap

No URL-level dedup is done in clustering. The `canonical_key` (SHA-256 of normalized URL) blocks exact URL duplicates at insert, but tracking-param variants (e.g., `?ref=twitter` vs `?utm_source=rss`) that canonicalize to the same path are handled by `normalize_url()` in ingestion but **not in clustering**. A small additional win: add URL host+path matching in the clustering pre-pass to catch reposts.
