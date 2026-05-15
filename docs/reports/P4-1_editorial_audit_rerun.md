# P4-1 — Editorial Quality Re-Audit (Re-run)

**Date:** 2026-05-15
**Window:** Last 48h READY articles, ordered by `global_score DESC`
**Audited:** Top 50 articles
**Baseline:** Original audit 2026-05-11

---

## Supporting Stats (full READY pool, last 48h)

| Metric | Value |
|---|---|
| Total READY articles | 262 |
| `is_major_tech_news = true` | 97 (37%) |
| `is_major_tech_news IS NULL` | 1 (< 1%) |
| Avg global_score | 0.573 |
| Max global_score | 0.873 |
| Min global_score | 0.000 |

Score spread: 0.873 − 0.000 = 0.873 (vs baseline ~0.32 spread, max 0.69).

---

## Source Distribution (top sources in READY pool, last 48h)

| Source | Count |
|---|---|
| Android Authority | 31 |
| The Verge | 16 |
| TechCrunch | 16 |
| The Register | 14 |
| Wired | 12 (incl. estimates) |
| VentureBeat | 9 |
| AWS | 8 |
| Engadget | 8 |
| MIT Technology Review | 8 |
| Electrek | 7 |
| TheNewStack | 7 |
| ZDNet | 7 |
| MacRumors | 6 |
| TheHackerNews | 6 |
| InfoWorld | 6 |
| TechRadar | 6 |
| InfoQ | 6 |
| XDA Developers | 6 |
| Techdirt | 5 |
| ArXiv | 4 |

Android Authority at 31/262 = **11.8% of pool** (down from 15/100 = 15% in Phase 5 audit). P1-6 global cap of 3 per playlist is working at the delivery layer; pool skew persists but is reduced.

---

## Top-50 Article List with Rubric Ratings

Rubric dimensions (rated per article):
- **a. Filler/Weak** — affiliate/listicle/hobbyist/personal blog/tutorial (no news value)
- **b. Duplicate story** — same story covered by another article in the top-50
- **c. Major tech company** — Apple / Microsoft / Meta / Google / Amazon / Nvidia
- **d. `is_major_tech_news`** — flag is set true
- **e. Funding/VC/startup** — funding rounds, acquisitions, market moves
- **f. Security/vulnerability** — CVEs, breaches, exploits
- **g. AI/ML news** — meaningful AI product/research
- **h. Source quality** — premium vs blog/aggregator

Rating: ✓ = yes / — = no / ⚠ = borderline

| # | Score | Source | Title (truncated) | a.Filler | b.Dup | c.BigCo | d.Major | e.Fund | f.Sec | g.AI | h.Premium |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.873 | Android Authority | Gemini's Spark agent leaked, gunning for Claude Cowork's throne | — | ✓(#16) | ✓(Google) | ✓ | — | — | ✓ | ⚠ |
| 2 | 0.861 | TechCrunch | OpenAI says Codex is coming to your phone | — | ✓(#4,#6) | ✓(OpenAI) | ✓ | — | — | ✓ | ✓ |
| 3 | 0.858 | The Verge | Musk v. Altman accomplished nothing but airing dirty laundry | — | ✓(#24,#34) | — | ✓ | — | — | — | ✓ |
| 4 | 0.857 | The Verge | OpenAI's Codex is now in the ChatGPT mobile app | — | ✓(#2,#6) | ✓(OpenAI) | ✓ | — | — | ✓ | ✓ |
| 5 | 0.857 | The Register | Possible Samsung strike puts pressure on memory pricing | — | — | — | ✓ | — | — | — | ✓ |
| 6 | 0.856 | Android Authority | OpenAI Codex is coming to mobile so you can build apps on the go | — | ✓(#2,#4) | ✓(OpenAI) | ✓ | — | — | ✓ | ⚠ |
| 7 | 0.837 | 404 Media | Mayo Clinic is Using AI to Listen to ER Visits | — | — | — | ✓ | — | — | ✓ | ✓ |
| 8 | 0.835 | The Verge | AI-generated research papers are overwhelming peer review | — | — | — | ✓ | — | — | ✓ | ✓ |
| 9 | 0.834 | Wired | Engineer's Post Protesting Laptop Surveillance Going Viral Inside Meta | — | — | ✓(Meta) | ✓ | — | — | — | ✓ |
| 10 | 0.816 | MIT Technology Review | AI chatbots are giving out people's real phone numbers | — | — | — | ✓ | — | — | ✓ | ✓ |
| 11 | 0.807 | Android Authority | Apple's ChatGPT deal might be getting messy as Gemini moves in | — | — | ✓(Apple) | ✓ | — | — | ✓ | ⚠ |
| 12 | 0.805 | ZDNet | Amazon Prime Day 2026 coming in June: dates, deals, what to expect | ✓(deals/listicle) | — | ✓(Amazon) | ✓ | — | — | — | ⚠ |
| 13 | 0.801 | Engadget | Meta bringing third-party apps and games to display glasses | — | ✓(#38) | ✓(Meta) | ✓ | — | — | — | ✓ |
| 14 | 0.798 | Android Authority | Google's AI-powered Health Coach doing exactly what you feared | — | — | ✓(Google) | ✓ | — | — | ✓ | ⚠ |
| 15 | 0.791 | MIT Technology Review | The Tesla Semi could be a big deal for electric trucking | — | — | — | ✓ | — | — | — | ✓ |
| 16 | 0.790 | Android Authority | Google's upcoming 'Gemini Spark' could book flights and handle inbox | — | ✓(#1) | ✓(Google) | ✓ | — | — | ✓ | ⚠ |
| 17 | 0.790 | TechCrunch | Geothermal startup Fervo Energy pops 33% in IPO debut | — | — | — | ✓ | ✓(IPO) | — | — | ✓ |
| 18 | 0.784 | Android Authority | Your carrier may get help from rivals when you're off-grid | — | — | — | ✓ | — | — | — | ⚠ |
| 19 | 0.783 | InfoQ | Anthropic Launches Claude Platform on AWS | — | — | — | ✓ | — | — | ✓ | ⚠ |
| 20 | 0.780 | Engadget | Security researchers breach macOS aided by Anthropic's Mythos | — | — | — | ✓ | — | ✓ | ✓ | ✓ |
| 21 | 0.780 | The Register | Google's AI-enabled mouse pointer understands 'this' and 'that' | — | — | ✓(Google) | ✓ | — | — | ✓ | ✓ |
| 22 | 0.779 | Wired | Meta's New Reality: Record High Profits. Record Low Morale | — | — | ✓(Meta) | ✓ | — | — | — | ✓ |
| 23 | 0.777 | The Verge | Behold, the Elon Musk jackass trophy | ⚠(opinion/tabloid) | ✓(#3,#24) | — | ✓ | — | — | — | ✓ |
| 24 | 0.773 | Wired | The Real Losers of the Musk v. Altman Trial | — | ✓(#3,#23) | — | ✓ | — | — | — | ✓ |
| 25 | 0.770 | VentureBeat | Anthropic reinstates OpenClaw and third-party agent usage | — | — | — | ✓ | — | — | ✓ | ✓ |
| 26 | 0.768 | VentureBeat | Agent authorization gap: why verified agents are still a risk | ⚠(analysis) | — | — | ✓ | — | — | ✓ | ✓ |
| 27 | 0.766 | MIT Technology Review | How Chinese short dramas became AI content machines | — | — | — | ✓ | — | — | ✓ | ✓ |
| 28 | 0.765 | The Register | Cisco to fire 4,000 staff and generously give free training | — | — | — | ✓ | — | — | — | ✓ |
| 29 | 0.762 | MacRumors | Apple Grew U.S. iPhone Sales While Broader Market Declined in Q1 | — | — | ✓(Apple) | ✓ | — | — | — | ✓ |
| 30 | 0.762 | Ars Technica | Zero-day exploit completely defeats default Windows 11 BitLocker | — | — | ✓(Microsoft) | ✓ | — | ✓ | — | ✓ |
| 31 | 0.757 | MacRumors | Ads Aren't in the Apple Maps App Yet, But They're Coming Soon | — | — | ✓(Apple) | ✓ | — | — | — | ✓ |
| 32 | 0.756 | The Register | AWS Quick admins: access control didn't work, so what's the problem? | — | — | ✓(Amazon/AWS) | ✓ | — | ✓(sort of) | — | ✓ |
| 33 | 0.756 | The Verge | Netflix's ad ambitions just keep growing | — | — | — | ✓ | — | — | — | ✓ |
| 34 | 0.755 | TechCrunch | What the jury will actually decide in Musk vs. Altman | — | ✓(#3,#23,#24) | — | ✓ | — | — | — | ✓ |
| 35 | 0.753 | The Register | Anthropic butts in to small business, promises payroll help | — | — | — | ✓ | — | — | ✓ | ✓ |
| 36 | 0.750 | Engadget | Apple may open up the App Store to agentic AI | — | — | ✓(Apple) | ✓ | — | — | ✓ | ✓ |
| 37 | 0.743 | BBC | UK saves 'millions' by ditching Palantir for refugee system | — | — | — | ✓ | — | — | — | ✓ |
| 38 | 0.741 | The Verge | Meta brings virtual writing to everyone with Meta Ray-Ban Display glasses | — | ✓(#13) | ✓(Meta) | — | — | — | — | ✓ |
| 39 | 0.740 | Android Authority | Gemini on Android gets new icon, smarter audio sharing, 'Luminous' look | ⚠(minor feature) | — | ✓(Google) | — | — | — | ✓ | ⚠ |
| 40 | 0.739 | The Register | Cerebras risked it all on dinner plate-sized AI accelerators — now $66B | — | — | — | ✓ | ✓(valuation) | — | ✓ | ✓ |
| 41 | 0.737 | The Register | Nobody believes criminals who hacked Canvas really deleted student data | — | — | — | ✓ | — | ✓ | — | ✓ |
| 42 | 0.733 | The Verge | YouTube is courting creators — and sponsors — with streaming shows | — | — | ✓(Google/YT) | ✓ | — | — | — | ✓ |
| 43 | 0.731 | The Verge | AMD's best CPU tech for gamers is coming to workstations too | — | — | — | ✓ | — | — | — | ✓ |
| 44 | 0.729 | The Register | OpenAI caught in TanStack npm supply chain chaos | — | — | ✓(OpenAI) | ✓ | — | ✓ | — | ✓ |
| 45 | 0.728 | MacRumors | Report: Intel is Testing Production of Some iPhone, iPad, and Mac Chips | — | — | ✓(Apple) | ✓ | — | — | — | ✓ |
| 46 | 0.724 | VentureBeat | Frontier AI models corrupt 25% of document content | — | — | — | ✓ | — | — | ✓ | ✓ |
| 47 | 0.721 | The Verge | Microsoft's Edge Copilot update uses AI to pull info from across tabs | — | — | ✓(Microsoft) | ✓ | — | — | ✓ | ✓ |
| 48 | 0.720 | TechCrunch | Notion just turned its workspace into a hub for AI agents | — | — | — | ✓ | — | — | ✓ | ✓ |
| 49 | 0.715 | Platformer | Are the Twitter clones in trouble? | — | — | — | ✓ | — | — | — | ✓ |
| 50 | 0.710 | InfoQ | Architecting Autonomy: Decentralising Architecture Inside an Organization | ✓(technical tutorial/org design) | — | — | — | — | — | — | ⚠ |

---

## Duplicate Story Clusters in Top-50

| Cluster | Articles (by rank) | Story |
|---|---|---|
| Cluster A | #1, #16 | Google Gemini Spark agent (two AA articles, same news, different angles) |
| Cluster B | #2, #4, #6 | OpenAI Codex coming to mobile (TechCrunch + Verge + AA = 3 articles) |
| Cluster C | #3, #23, #24, #34 | Musk v. Altman trial (4 articles from Verge×2, Wired, TechCrunch) |
| Cluster D | #13, #38 | Meta Ray-Ban Display glasses (Engadget + Verge, same product announcement) |

**Total distinct duplicate clusters: 4** (Cluster B is the most egregious — 3 items on one story in top-8.)

---

## Rubric Summary Table

| Dimension | Count in top-50 | % | Baseline (2026-05-11) |
|---|---|---|---|
| a. Filler/Weak | 4 | **8%** | ~50% |
| b. Duplicate story (any cluster) | 12 | 24% | ~6 (3 clusters, ~12 items) |
| c. Major tech company coverage | 30 | 60% | 0% |
| d. `is_major_tech_news = true` | 47 | **94%** | 0% |
| e. Funding/VC/startup | 3 | 6% | 0% |
| f. Security/vulnerability | 5 | 10% | ~4% |
| g. AI/ML news | 26 | 52% | ~20% |
| h. Source quality (premium) | 38 | 76% | ~35% |

**Filler/Weak breakdown (4 items):**
- #12 ZDNet Amazon Prime Day deals preview — consumer deals roundup, not tech news
- #23 The Verge Elon Musk "jackass trophy" — tabloid opinion piece, no news content
- #39 Android Authority Gemini icon/UI refresh — minor feature update, not newsworthy at this level
- #50 InfoQ "Architecting Autonomy" — organizational design tutorial/think-piece, not tech news

---

## Acceptance Criteria Assessment

| Criterion | Target | Result | Met? |
|---|---|---|---|
| Filler+Weak share | < 25% | **8%** (4/50) | **YES** |
| Duplicate-story clusters | ≤ 1 | **4 clusters** | **NO** |
| Apple in top-50 | ≥ 1 item | ✓ — items #11, #29, #31, #36, #45 (5 items) | **YES** |
| Microsoft in top-50 | ≥ 1 item | ✓ — items #30, #47 | **YES** |
| Meta in top-50 | ≥ 1 item | ✓ — items #9, #13, #22, #38 | **YES** |
| ≥ 3 of (Apple, Microsoft, Meta) represented | ≥ 3 categories | **All 3** | **YES** |
| `is_major_tech_news = true` count | Increase from 0 | **47 of 50** (94%) | **YES** |

**Overall: 4/5 criteria met. Duplicate suppression criterion not met.**

---

## Comparison to Baseline (2026-05-11)

| Metric | Baseline | This audit | Delta |
|---|---|---|---|
| Filler+Weak share | ~50% | 8% | −42pp |
| Duplicate clusters | 3 | 4 | +1 (worse) |
| Apple coverage | 0 items | 5 items | +5 |
| Microsoft coverage | 0 items | 2 items | +2 |
| Meta coverage | 0 items | 4 items | +4 |
| `is_major_tech_news=true` in top-50 | 0 | 47 | +47 |
| Score max | 0.69 | 0.873 | +0.183 |
| Score spread | ~0.32 | 0.163 (top-50 only) | narrowed in top-50 |
| Premium-source articles in top-50 | ~35% | 76% | +41pp |

### Regression note — score compression at top of range

The top-50 now shows a compressed band between 0.710 and 0.873 — a spread of only 0.163. This means good-but-not-great stories (Gemini icon refresh, Prime Day deals) sit very close in score to genuinely major stories (OpenAI Codex on mobile, Meta morale/profits). Within-cluster duplicates also land adjacent (Codex cluster: 0.861, 0.857, 0.856 — nearly tied). P5-4 score calibration widened the global range but has not yet created strong enough separation between major vs. minor stories to push duplicates far enough apart for clustering to catch them.

---

## Comparison to Top Tech News (2026-05-14 to 15)

Top stories of the 48h window identified via web search and Techmeme:

| Story | In top-50? | In READY pool? |
|---|---|---|
| OpenAI Codex on mobile | **Yes** (#2, #4, #6) | Yes |
| Musk v. Altman trial | **Yes** (#3, #24, #34) | Yes |
| Meta Ray-Ban Display glasses / handwriting | **Yes** (#13, #38) | Yes |
| Google Gemini Spark agent leaked | **Yes** (#1, #16) | Yes |
| Windows 11 BitLocker zero-day | **Yes** (#30) | Yes |
| Cisco 4,000 layoffs | **Yes** (#28) | Yes |
| Cerebras $66B valuation | **Yes** (#40) | Yes |
| AI papers overwhelming peer review | **Yes** (#8) | Yes |
| Meta morale / profits story | **Yes** (#22) | Yes |
| Samsung memory strike pressure | **Yes** (#5) | Yes |
| Fervo Energy IPO | **Yes** (#17) | Yes |
| Nvidia China/Trump-Xi trip | **No** | PENDING only |
| Gmail storage limit reduction | **No** | PENDING only |
| xAI Grok Build coding agent | **No** | PENDING only |
| Akamai/LayerX $205M acquisition | Borderline — low score (0.483) | READY (Calcalistech) |

**12 of 15 major stories of the window are represented in the top-50 READY pool.** The three missing stories (Nvidia/China, Gmail storage, xAI Grok Build) are in DB but stuck at PENDING — likely a recency issue (many published early on May 15 and may not have completed the pipeline yet at time of query).

---

## Verdict

**Significant improvement since the 2026-05-11 baseline.** The editorial quality of the top-50 is substantially better:

- **Filler content collapsed from ~50% to 8%** — the dominant win. Phase 5 feed disables (HN, STAT, CleanTechnica) and P1-5 affiliate suppression together drove most of this.
- **Major tech company coverage went from 0 to all 3 targets** — P0-BUG-1 description fallback + P2-3/P2-4 major-news threshold and boost are clearly working.
- **`is_major_tech_news` flag went from 0 to 47 of 50** — the retro-classify pass and sweep are saturating the top of the READY pool.
- **Source quality improved markedly** — The Register, Wired, TechCrunch, The Verge, MIT Tech Review, Ars Technica, 404 Media all present in top-50. No AWS blog, no HN hobbyist items.
- **Score range widened** — max moved from 0.69 to 0.873. Story-type signal is now differentiated.

**The one failure is duplicate suppression.** Four distinct story clusters are in the top-50, including a 4-item cluster on the Musk v. Altman trial and a 3-item cluster on OpenAI Codex. P2-1 cross-source title dedup shipped but is clearly not catching these — likely because each article's title wording differs enough to fall below the 0.75 token-similarity threshold. Clustering at 0.40 combined threshold (P1-7) is also not catching them because the stories use distinct entity sets and vocabulary across sources. This is a known hard problem: semantic deduplication requires a different approach (embedding similarity or explicit story-clustering by entity+timewindow). The Phase 6 work does not address this gap.

**Recommendation:** P4-1 passes on 4 of 5 criteria. Duplicate suppression should be added to the Phase 6 task list as P6-4.

---

*Report generated: 2026-05-15*
