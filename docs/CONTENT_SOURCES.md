# Content Sources

All RSS feeds and YouTube channels that power the Blips News feed.

**Last updated:** April 2026

> **AI Feed Governance** — AI-category items are capped at **40 % of any visible feed window**. No more than **2 consecutive AI items** may appear in sequence. Multiple publishers covering the same AI story are collapsed into a single cluster; the representative source is chosen by `global_score`. See `app/config/diversity.py` for enforcement logic and `tests/unit/test_ai_coverage.py` for cap tests.

> **Inventory Health** — Category and source distribution are measured continuously via `GET /metrics/inventory/health`. Alerts fire when any single source exceeds **30 %** of the promoted window or infrastructure topics fall below **10 %** combined share. Validated by `tests/unit/test_feed_distribution.py`.

---

## RSS Feeds

### Breaking News

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| TechCrunch | Premium | 4 | Top tech news, startup coverage, product launches |
| The Verge | Premium | 4 | Consumer tech, gadgets, digital culture |
| Ars Technica | Premium | 3 | In-depth tech journalism, science, policy |
| 9to5Mac | Premium | 3 | Apple ecosystem news, leaks, reviews |
| 9to5Google | Premium | 3 | Google/Android ecosystem news, Pixel, Chrome |
| Engadget | Standard | 3 | Gadget news and reviews |
| CNET | Standard | 2 | Consumer tech news and reviews |
| ZDNet | Standard | 2 | Enterprise and consumer tech news |
| MacRumors | Standard | 1 | Apple rumors, product launches, buying guides |
| Digital Trends | Standard | 1 | Consumer tech, lifestyle tech, buying guides |
| TechRadar | Standard | 1 | Reviews, deals, consumer tech news |

### Analysis & Context

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| MIT Technology Review | Premium | 3 | Academic rigor, emerging tech, AI research |
| IEEE Spectrum | Premium | 2 | Engineering perspective, technical depth |
| The Atlantic (Tech) | Premium | 2 | Tech policy, society, long-form analysis |
| The Information | Premium | 1 | Premium tech business journalism, scoops |
| Wired | Premium | 3 | Tech culture, long-form features, analysis |
| Android Authority | Standard | 1 | Android reviews, tutorials, buying guides |
| Tom's Hardware | Standard | 2 | PC hardware benchmarks, GPU reviews |

### AI & Machine Learning — Primary Sources (Official Blogs)

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| OpenAI Blog | Premium | 2 | Model releases, safety research, product launches |
| Anthropic Blog | Premium | 2 | Claude updates, alignment research, interpretability |
| Google DeepMind Blog | Premium | 2 | Gemini, AlphaFold, fundamental AI research |
| Meta AI Blog | Premium | 2 | Llama family, open-source AI, infrastructure |
| Microsoft AI Blog | Premium | 2 | Copilot, Azure AI, responsible AI posts |
| Hugging Face Blog | Premium | 2 | OSS model releases, datasets, inference |
| Simon Willison's Blog | Premium | 1 | LLM tools, prompt engineering, AI analysis |

### AI & Machine Learning — Research

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| Papers With Code (trending) | Research | 2 | State-of-the-art results with reproducible code |
| Stanford HAI | Research | 1 | Human-centred AI policy, interdisciplinary research |
| MIT CSAIL | Research | 1 | Applied CS research from MIT's AI lab |

> **Research feed policy** — Papers With Code is filtered to trending/state-of-the-art results only. Items must pass the standard promotion scorer before entering the feed. Daily caps are strict to prevent research flooding.

> **Note:** arXiv cs.AI is currently disabled (removed March 2026). Volume and signal-to-noise ratio require keyword filtering before re-enabling.

### AI & Machine Learning — Infrastructure & Chips

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| NVIDIA AI Blog | Standard | 2 | GPU architecture, CUDA, AI hardware announcements |
| AWS Machine Learning Blog | Standard | 2 | SageMaker, Bedrock, cloud ML tooling |
| Azure AI Blog | Standard | 1 | Azure OpenAI Service, Copilot stack, responsible AI |
| SemiAnalysis | Premium | 1 | Deep chip architecture analysis, TPU/GPU economics |

### Infrastructure & Cloud

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| The New Stack | Premium | 3 | Cloud native, Kubernetes, DevOps |
| InfoQ | Premium | 3 | Software architecture, enterprise patterns |
| CNCF Blog | Standard | 2 | Cloud Native Computing Foundation — K8s ecosystem, CNCF projects |
| Cloudflare Blog | Premium | 2 | Network infrastructure, edge computing, security engineering |
| Kubernetes Blog | Standard | 1 | Official K8s blog — release notes, deep dives, community |
| HashiCorp Blog | Standard | 1 | Terraform, Vault, Nomad — infra-as-code and secrets management |

### Startups & Business

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| VentureBeat | Premium | 3 | AI, enterprise tech, gaming industry |
| Crunchbase News | Standard | 2 | Startup funding, valuations, M&A |

### Security & Privacy

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| Krebs on Security | Premium | 2 | Investigative security journalism |
| The Hacker News | Standard | 3 | Security news, vulnerabilities, breaches |
| Dark Reading | Standard | 2 | Enterprise security, threat intelligence |

### Developer

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| Smashing Magazine | Standard | 2 | Web development, design, UX |
| XDA Developers | Standard | 1 | Mobile dev, Android mods, phone reviews |
| Product Hunt | Standard | 2 | New product launches, indie tools |
| Hacker News | Supplemental | 3 | Community-driven, variable quality |

### Primary Sources (Official Blogs)

| Source | Tier | Daily Cap | Notes |
|--------|------|-----------|-------|
| Google AI Blog | Supplemental | 2 | Official AI announcements |
| Microsoft Blog | Supplemental | 2 | Official Microsoft announcements |

**Disabled feeds:** Google Cloud Blog, Apple Newsroom, CSS-Tricks, The Batch (Andrew Ng)

*Previously disabled feeds now re-enabled:* OpenAI Blog (re-enabled March 2026 under AI Primary tier), AWS Blog (re-enabled in Infrastructure tier as AWS ML Blog).

---

## YouTube Channels

### Explainer / Review

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| Marques Brownlee (MKBHD) | Long-form | Premium | 2 | Top tech reviewer |
| ShortCircuit | Long-form | Premium | 3 | Quick reviews, unboxings (LTT family) |
| TechLinked | Long-form | Premium | 2 | Daily tech news digest (LTT family) |
| Linus Tech Tips | Mixed | Premium | 2 | Comprehensive tech reviews and builds |
| Dave2D | Long-form | Premium | 2 | Clean laptop/phone reviews |
| Mrwhosetheboss | Mixed | Premium | 2 | High production tech reviews |
| MrMobile (Michael Fisher) | Long-form | Premium | 1 | Mobile-focused reviews, retro tech |
| Unbox Therapy | Mixed | Standard | 2 | Gadget unboxings and reviews |
| Austin Evans | Long-form | Standard | 2 | Gaming and tech reviews |
| Hardware Canucks | Long-form | Standard | 2 | PC hardware reviews |
| Computerphile | Long-form | Standard | 2 | Computer science explanations |
| SuperSaf | Mixed | Standard | 2 | Phone comparisons, camera tests |
| iJustine | Long-form | Standard | 1 | Apple ecosystem, lifestyle tech |
| Flossy Carter | Long-form | Standard | 1 | Unfiltered phone reviews |
| JerryRigEverything | Mixed | Standard | 2 | Durability tests, teardowns |
| Karl Conrad | Mixed | Standard | 2 | Cinematic tech reviews, camera comparisons |
| Snazzy Labs | Mixed | Standard | 1 | Apple ecosystem deep dives, smart home |
| Sam Beckman | Mixed | Standard | 2 | Phone speed tests, comparisons |

### News / Commentary

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| Bloomberg Technology | Long-form | Premium | 4 | Professional tech news coverage |
| The Wall Street Journal | Long-form | Premium | 3 | In-depth tech business analysis |
| The Verge | Mixed | Premium | 3 | Tech news and reviews |
| Fireship | Mixed | Premium | 2 | Developer news and quick explainers |
| CNBC | Long-form | Standard | 3 | Business/tech news intersection |
| Mental Outlaw | Long-form | Standard | 2 | Linux and privacy tech news |

### Engineer / Deep Tech

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| Two Minute Papers | Long-form | Premium | 2 | AI/ML research paper summaries |
| 3Blue1Brown | Long-form | Premium | 1 | Math/CS visualizations |
| The Net Ninja | Long-form | Standard | 2 | Web development tutorials |

### AI / ML Focused

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| Matt Wolfe | Mixed | Premium | 2 | AI tools roundups, weekly AI news |
| AI Explained | Long-form | Premium | 1 | Deep AI research analysis, benchmarks |
| Andrej Karpathy | Long-form | Premium | 1 | Neural network deep-dives, AI education |
| Yannic Kilcher | Long-form | Standard | 1 | AI paper walkthroughs and critiques |
| Lex Fridman | Long-form | Standard | 1 | Long-form researcher/engineer interviews |

### Official

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| OpenAI | Long-form | Supplemental | 2 | Official AI announcements |
| Google Developers | Long-form | Supplemental | 2 | Google tech announcements |
| Android Developers | Mixed | Supplemental | 2 | Android platform updates |
| Microsoft Developer | Long-form | Supplemental | 2 | Microsoft tech announcements |
| Apple | Long-form | Supplemental | 2 | Apple official announcements |

### Shorts / Reels

| Channel | Tier | Daily Cap | Notes |
|---------|------|-----------|-------|
| Tech Vision | Standard | 5 | Tech news shorts |
| Jeff Geerling | Standard | 3 | Raspberry Pi and hardware shorts |
| Technology Connections Shorts | Premium | 2 | Tech history and explainer shorts |

**Disabled channels:** Ben Eater, freeCodeCamp, NileRed Shorts, and duplicate shorts entries for MKBHD/Mrwhosetheboss/Unbox Therapy (handled via mixed format on main entry)

*Previously disabled channels now re-enabled:* NVIDIA (re-enabled in AI/ML Focused tier, 1 video/day cap), Amazon Web Services (re-enabled under infrastructure coverage, 1 video/day cap).

### AI / ML Focused — Official (re-enabled)

| Channel | Format | Tier | Daily Cap | Notes |
|---------|--------|------|-----------|-------|
| NVIDIA | Mixed | Supplemental | 1 | GPU announcements, AI research demos |
| Amazon Web Services | Long-form | Supplemental | 1 | AWS AI/ML service launches |

---

## Totals

| Type | Enabled Sources | Total Daily Cap |
|------|-----------------|------------------|
| RSS Feeds | ~49 | ~96 articles/day |
| YouTube (Long-form + Mixed) | ~40 | ~80 videos/day |
| YouTube (Shorts + Mixed) | ~17 | ~44 reels/day |

After deduplication and quality filtering, the target output is approximately **50–55 articles** and **30–40 videos/reels** per day.

### AI Coverage Breakdown (target)

| Metric | Target |
|--------|--------|
| AI share of article feed | ≤ 40 % |
| Max consecutive AI articles | 2 |
| AI source diversity (distinct sources in window) | ≥ 3 |
| AI cluster collapse rate | > 60 % of duplicate AI stories clustered |

These targets are enforced at runtime by `DiversityMixer` category-cap logic and validated by `tests/unit/test_ai_coverage.py`.

### Feed Distribution Targets

| Metric | Target |
|--------|--------|
| Max single-source share (50-item window) | ≤ 30 % |
| Infrastructure topic share (Cloud + DevOps + Security) | ≥ 10 % |
| ROLE_QUOTAS: BREAKING | 12/day |
| ROLE_QUOTAS: AI | 12/day |
| ROLE_QUOTAS: INFRA | 8/day |
| ROLE_QUOTAS: SECURITY | 5/day |
| ROLE_QUOTAS: DEV | 5/day |

Distribution is validated by `tests/unit/test_feed_distribution.py` and monitored in production via `GET /metrics/inventory/health`.
