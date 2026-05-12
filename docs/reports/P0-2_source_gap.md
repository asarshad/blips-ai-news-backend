# P0-2 — Source Coverage Gap Test
**Date:** 2026-05-11 | **Status:** COMPLETE

## Summary

The source coverage gap is **less severe than the prior audit suggested**, but a worse problem was found: **many important stories ARE ingested but are stuck in the pipeline, never reaching READY**. The gap is primarily a promotion/pipeline bug, not just a missing-feed problem.

---

## Techmeme Benchmark Headlines (May 11, 2026 snapshot)

| # | Techmeme headline (original source) | In our DB? | Status | Score |
|---|---|---|---|---|
| 1 | Amazon employees gaming AI token targets with MeshClaw (FT) | No | — | — |
| 2 | US House probe into Sam Altman's personal investments (WSJ) | No | — | — |
| 3 | Grok downloads fell to 8.3M in April from 20M+ in January (WSJ) | No | — | — |
| 4 | OpenAI/Microsoft cap revenue sharing at $38B (The Information) | No | — | — |
| 5 | Commerce Dept removed AI model test agreement from website (Reuters) | No | — | — |
| 6 | Q&A with Cognition/Devin CEO Scott Wu, $445M ARR (Colossus) | No | — | — |
| 7 | npm supply chain attack: Mistral, UiPath, TanStack packages compromised (Socket) | No | — | — |
| 8 | GM IT layoffs to hire AI-skilled staff (Bloomberg) | Yes (TechCrunch) | READY | 0.70 |
| 9 | OpenAI launches Daybreak cybersecurity initiative (Engadget) | Yes | READY | 0.51 |
| 10 | Thinking Machines Lab interaction models (TMlab blog) | No | — | — |
| 11 | Robinhood Venture Fund II confidential filing (Axios) | No | — | — |
| 12 | Digg relaunches as AI news aggregator (TechCrunch) | No | — | — |
| 13 | GitLab announces layoffs and country cuts (Bloomberg/Sarah Frier) | Yes (Simon Willison) | READY | 0.60 |
| 14 | Musk v. Altman: Satya Nadella testifies (CNBC) | Yes | **CANDIDATE** | 0.59 |
| 15 | Musk v. Altman: Ilya Sutskever stake/ouster testimony (Bloomberg/Wired) | Yes (Wired) | READY | 0.53 |

**Ingested: 6/15 (40%) | READY: 4/15 (27%)**

---

## Critical Stories Ingested But Stuck

These stories ARE in the database but are not reaching users:

| Story | Source in DB | Status | Score | Why stuck |
|---|---|---|---|---|
| RCS E2EE rolling out to iPhone/Android | CNET, Engadget, 9to5Mac, 9to5Google | CANDIDATE/PENDING | 0.54–0.61 | Never promoted — no promotion_score computed |
| Satya Nadella testifies in Musk v. Altman | CNBC Technology | CANDIDATE/PENDING | 0.59 | **is_major_tech_news=true** but stuck at CANDIDATE |
| Apple↔Intel chip deal | CNBC Technology, Reuters, 9to5Mac, Digital Trends | CANDIDATE/PENDING | 0.45–0.58 | Multiple versions stuck, Reuters ingested but never promoted |
| Intel/Apple: CNBC Technology analysis | CNBC Technology | CANDIDATE/PENDING | 0.58 | **is_major_tech_news=true** — stuck |
| Helsing $1.2B raise | TechCrunch | READY | 0.51 | READY but scores too low to enter top-50 |
| OpenAI trial multiple analysis pieces | CNBC Technology | CANDIDATE/PENDING | 0.48–0.51 | **is_major_tech_news=true** on several — all stuck |

## The CNBC Technology Pipeline Bug — CRITICAL

This is the most important finding of all Phase 0 work:

```
CNBC Technology: 99 total items
  CANDIDATE: 99   (100%)
  READY:      0   (0%)
```

CNBC Technology has **never produced a single READY article** despite 99 items ingested and several having `is_major_tech_news=true` and `global_score` in the 0.47–0.59 range. The items have no `promotion_score` computed — they're bypassing the promotion service entirely.

This is not an editorial quality problem. It's a **pipeline bug where CNBC Technology items are ingested but never enter the promotion queue**.

Likely cause: CNBC Technology may be added to the RSS registry under a role/label not covered by the promotion service's CANDIDATE-fetch query, or there's a source-level suppression/skip flag active.

**This one fix — unblocking CNBC Technology's promotion path — would add up to 99 queued articles to the pipeline, many of which are high-importance stories already flagged as major news.**

---

## Actual Source Coverage Gaps (stories never ingested)

Stories that are genuinely absent because the source doesn't exist in our registry:

| Story | Source | Registry status |
|---|---|---|
| Amazon MeshClaw AI token gaming | FT (Financial Times) | Not in registry |
| House probe into Sam Altman investments | WSJ | Not in registry |
| Grok downloads decline | WSJ / AppMagic | Not in registry |
| OpenAI/Microsoft $38B revenue cap | The Information | Enabled but 403-dead |
| Commerce Dept AI agreement removed | Reuters | Not in registry (item appeared as signal stub) |
| npm supply chain attack (Shai-Hulud) | Socket | Not in registry |
| Robinhood Venture Fund II | Axios | Not in registry |
| Digg relaunch | TechCrunch | **Should be in DB — investigate** |

---

## Techmeme Recommendation

Adding `https://www.techmeme.com/feed.xml` as MAJOR_NEWS / PREMIUM would give headline-level coverage of FT, WSJ, Reuters, Bloomberg, and The Information stories — all the gaps that can't be filled by direct feed subscription. The Techmeme feed titles explicitly attribute the original source (e.g. "Source: [headline] (Financial Times)").

---

## Revised Gap Assessment

| Gap type | Items | Primary fix |
|---|---|---|
| Pipeline bug (CNBC Technology stuck) | 99 items | Fix promotion eligibility for CNBC Technology source |
| Stories ingested but ranked too low | ~10–15 items/day | Co-mention boost + major-news score boost |
| Stories not ingested (premium paywall sources) | ~8–10/day | Add Techmeme; fix The Information 403 |
| Stories not ingested (niche sources) | ~3–5/day | Add Socket, Axios, specific feeds |
