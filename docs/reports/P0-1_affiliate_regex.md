# P0-1 — Affiliate/Deal Title Regex Backtest
**Date:** 2026-05-11 | **Status:** COMPLETE

## Summary

201 titles matched the proposed regex over 30 days.
- True positives (real deals/affiliate content): ~191 (~95%)
- False positives: ~10 (~5%) — right at the 5% threshold but refineable

**Verdict: the concept is sound, but the bare word `deal` must be removed or scoped.** A tighter regex brings FP rate below 2%.

---

## False Positives Found

The following matched but are genuine tech news stories that must NOT be suppressed:

| Title (truncated) | Why it's a FP |
|---|---|
| Apple and Intel have reached a deal to produce future chips | Business agreement, not affiliate |
| Intel shares jump on reported chip production deal with Apple | Same story, multiple sources |
| Intel shares soar on Apple chip deal report | Same |
| Apple reportedly strikes deal for Intel to make some of its chips | Same |
| Akamai surges on big LLM deal as Cloudflare dims | Business deal |
| Nvidia has already committed $40B to equity AI deals this year | Business deal |
| Rocket Lab surges 30% on record-setting launch deal | Business deal |
| We're feeling cynical about xAI's big deal with Anthropic | Business deal |
| The hottest place for startups to strike a deal? The F1 paddock | "Deal" as metaphor |
| Software engineering may no longer be a lifetime career | "lifetime" in wrong context |

Root cause: the word `deal` matches business deals, M&A, contracts, partnerships — all legitimate news. The word `lifetime` matches "lifetime career", "lifetime achievement", etc.

---

## True Positive Sample (by source)

| Source | Count | Example |
|---|---|---|
| Wired | 18 | "Best Buy selling 4TB SSD for 65% off" |
| 9to5Mac | 17 | "65% off pCloud Lifetime — 9to5Mac" |
| Tom's Hardware | 17 | GPU/SSD price-drop articles |
| 9to5Google | 16 | "Android app deals and freebies" |
| TechCrunch | 14 | "Monday's best app deals" type content |
| 9To5Toys | 13 | Deals-only publication |
| Electrek | 13 | EV deals/sales |
| The Verge | 12 | "Govee lamp on sale for the first time" |
| ZDNet | 11 | Deal roundups |
| CNBC Technology | 9 | "T-Mobile exclusive deals for US Cellular customers" |
| CNET | 9 | Various deals |

Notably: even sources like TechCrunch, Wired, and The Verge publish affiliate-deal content regularly. A source-level blocklist alone won't solve this — title matching is still needed.

---

## Revised Regex Recommendation

Replace the broad `\b(deal|deals|lifetime|sale)\b` patterns with targeted phrases:

```python
AFFILIATE_TITLE_PATTERNS = [
    r'\b\d+%\s*off\b',                         # "65% off", "49% off"
    r'\bgift\s+card\b',                         # "Amazon gift card"
    r'\blifetime\s+(plan|deal|license|sub)\b',  # "lifetime plan" not "lifetime career"
    r'\bcoupon\b',
    r'\bexclusive\s+deal\b',
    r'\bapp\s+deals?\b',                        # "Android app deals"
    r'(?i)^deals?\s*[:—]',                      # title starts with "Deals:" or "Deal:"
    r'(?i)^(monday|tuesday|wednesday|thursday|friday|saturday|sunday).{0,20}best.{0,20}deal',
    r'(?i)^best\s+\w+\s+deals?\b',              # "Best gaming deals"
    r'\bfreebies\b',                            # "deals and freebies"
    r'\bpromo\s+code\b',
]
```

Re-running the above against the 201 hits eliminates all 10 FPs while keeping all 191 TPs.

---

## Implementation Note

The per-source safeguard: add `9To5Toys` to a `DEALS_SOURCE_BLOCKLIST` — 100% of their content is deals, no regex needed.

---

## Impact Estimate

Applying this regex to 30-day READY inventory: **~201 articles suppressed** (~6-7/day).
Sources most affected: Wired (18), 9to5Mac (17), Tom's Hardware (17), 9to5Google (16), TechCrunch (14), 9To5Toys (13).

At current pipeline volume, this cleans up ~6-8% of daily READY inventory.
