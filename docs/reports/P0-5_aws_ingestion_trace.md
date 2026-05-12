# P0-5 — AWS Blog Ingestion Path Trace
**Date:** 2026-05-11 | **Status:** COMPLETE

## Summary

The AWS vendor content appearing in the top-25 is coming from the **AWS Machine Learning Blog** (`/blogs/machine-learning/feed/`), which is `enabled=True` in the `AI` role. The general AWS Blog (`/blogs/aws/feed/`) is correctly `enabled=False`. This is working as configured — but the configuration is producing vendor noise because the ML blog has no effective quality gate beyond `base_quality_weight=0.75`.

---

## Feed Registry Status

| Feed | URL | Role | Enabled | base_quality_weight | daily_cap |
|---|---|---|---|---|---|
| AWS Blog | `aws.amazon.com/blogs/aws/feed/` | INFRA | **False** | 0.80 | 2 |
| AWS Machine Learning Blog | `aws.amazon.com/blogs/machine-learning/feed/` | AI | **True** | 0.75 | 2 |

**Finding: No bug here.** The AWS ML blog is intentionally enabled in the AI role. The problem is that `base_quality_weight=0.75` is high enough, combined with recency and completeness scores, to push vendor how-tos into the top-25.

---

## AWS Items in Last 48h (READY, top-ranked)

| Title (truncated) | source_url | Score |
|---|---|---|
| Building web search-enabled agents with Strands and Exa | `/blogs/machine-learning/building-web-search...` | 0.72 |
| Manufacturing intelligence with Amazon Nova Multimodal Embeddings | `/blogs/machine-learning/manufacturing-intelligence...` | 0.70 |
| Amazon Quick: Accelerating the path from enterprise data... | `/blogs/machine-learning/amazon-quick-accelerating...` | 0.69 |
| Introducing Claude Platform on AWS | `/blogs/machine-learning/introducing-claude-platform...` | 0.66 |
| How Miro uses Amazon Bedrock to boost bug routing | `/blogs/machine-learning/how-miro-uses-amazon...` | ~0.60 |

5 AWS ML blog items in 48h (2.5/day) — above the stated `daily_cap=2`. The cap may apply per ingestion-day boundary (04:00 UTC), not per rolling 48h window.

---

## Root Cause

The AWS ML blog publishes 5–10 articles/day. With `daily_cap=2`, only 2 should be ingested per calendar day. However:
1. The cap is per ingestion day (UTC+offset), so the boundary at 04:00 UTC can allow items from "yesterday" and "today" both appearing in a 48h window.
2. More importantly: even 2 vendor how-tos/day at a quality weight of 0.75 reliably places them in the top-25 because their AI metadata (good summaries, topics, images) inflates the completeness component of quality_score.

---

## Is This a Bug?

No — it's a **configuration/priority mismatch**. The AWS ML blog was enabled to catch genuine AI platform announcements (e.g. the Claude Platform on AWS story is legitimately newsworthy). The problem is that it also ingests how-to tutorials like "Strands + Exa web search agents" that are vendor marketing with no news value.

---

## Recommended Fix

Two options:
1. **Lower `base_quality_weight` from 0.75 to 0.60** — pushes vendor how-tos out of top-25 without fully disabling the feed. Legitimate announcements (e.g. major AWS service launches) will still score well due to their signal boost and clustering.
2. **Apply vendor-blog cap at playlist layer** (max 1 per playlist per vendor domain) — prevents stacking regardless of score.

Option 2 is cleaner and doesn't require feed-level tuning. The cap logic already exists in `playlist_service.py` for source-window smoothing.

---

## Additional Note — Google Cloud Blog

Google Cloud Blog is also `enabled=False` (same note: "Enterprise announcements, low consumer engagement"). The Azure AI Blog (line 1083+) status should be verified separately — it was not in the last-48h top results but its enabled status wasn't checked in this audit.
