# RSS Ingestion System

This document describes the role-based RSS feed ingestion system for Blips.

## Overview

The RSS ingestion system fetches articles from 24+ tech publications and blogs,
targeting **35-40 high-quality, non-repetitive articles per day**.

### Design Goals

1. **Diversity** - Balanced coverage across breaking news, analysis, infrastructure, security, business, and developer content
2. **Quality** - Role-based quality weights prevent low-quality content from dominating
3. **Freshness** - Decay profiles ensure timely content surfaces appropriately
4. **No Repetition** - Per-feed daily caps and deduplication prevent source dominance

---

## Editorial Roles

Each feed is assigned an editorial role that defines its purpose in the content mix:

| Role | Description | Target/Day | Decay |
|------|-------------|------------|-------|
| **BREAKING** | Fast-moving news, product launches, industry updates | 10-12 | Fast (6h) |
| **ANALYSIS** | Deep dives, opinion, long-form context | 6-8 | Slow (48h) |
| **INFRA** | Cloud, DevOps, backend systems, enterprise | 4-5 | Normal (24h) |
| **SECURITY** | Cybersecurity, privacy, threats, vulnerabilities | 3-4 | Normal (24h) |
| **BUSINESS** | Startups, funding, acquisitions, market analysis | 3-4 | Normal (24h) |
| **DEV** | Developer tools, tutorials, programming trends | 3-4 | Slow (48h) |
| **PRIMARY** | Official company blogs (down-ranked) | 2-3 | Normal (24h) |

---

## Quality Tiers

Quality tiers apply ranking weight modifiers:

| Tier | Modifier | Description |
|------|----------|-------------|
| **PREMIUM** | 1.15x | Top-tier publications with strong editorial standards |
| **STANDARD** | 1.0x | Reliable sources with good coverage |
| **SUPPLEMENTAL** | 0.85x | Niche or variable quality, useful for diversity |

---

## Decay Profiles

Decay profiles control how quickly articles lose ranking weight over time:

| Profile | Half-Life | Use Case |
|---------|-----------|----------|
| **FAST** | 6 hours | Breaking news - stale quickly |
| **NORMAL** | 24 hours | Standard news cycle |
| **SLOW** | 48 hours | Analysis/evergreen - stays relevant longer |

---

## Feed Registry

### Breaking News

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| TechCrunch | Premium | 4 | Top tech news, startup coverage |
| The Verge | Premium | 4 | Consumer tech, gadgets, digital culture |
| Engadget | Standard | 3 | Gadget news and reviews |
| CNET | Standard | 3 | Consumer tech news |
| ZDNet | Standard | 3 | Enterprise and consumer tech |
| Ars Technica | Premium | 3 | In-depth tech journalism |

### Analysis & Context

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| Wired | Premium | 3 | Tech culture, long-form features |
| MIT Technology Review | Premium | 3 | Academic rigor, emerging tech |
| IEEE Spectrum | Premium | 2 | Engineering perspective |
| The Atlantic (Tech) | Premium | 2 | Tech policy, society |

### Infrastructure & Cloud

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| The New Stack | Premium | 3 | Cloud native, Kubernetes, DevOps |
| InfoQ | Premium | 3 | Software architecture, enterprise |
| AWS Blog | Standard | 2 | AWS announcements |
| Google Cloud Blog | Standard | 2 | GCP announcements |

### Startups & Business

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| VentureBeat | Premium | 3 | AI, enterprise tech, gaming |
| Crunchbase News | Standard | 2 | Startup funding, valuations |
| PitchBook | Standard | 2 | VC/PE deals, market data |

### Security & Privacy

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| Krebs on Security | Premium | 2 | Investigative security journalism |
| The Hacker News | Standard | 3 | Security news, vulnerabilities |
| Dark Reading | Standard | 2 | Enterprise security |

### Developer Perspective

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| Hacker News | Supplemental | 3 | Community-driven, variable quality |
| Smashing Magazine | Standard | 2 | Web development, design |
| CSS-Tricks | Standard | 2 | Frontend development |

### Primary Sources (Official Blogs)

These are **down-ranked** unless corroborated by news coverage:

| Feed | Quality Tier | Daily Cap | Notes |
|------|--------------|-----------|-------|
| OpenAI Blog | Supplemental | 2 | Down-ranked (0.70 weight) |
| Google AI Blog | Supplemental | 2 | Down-ranked (0.70 weight) |
| Microsoft Blog | Supplemental | 2 | Down-ranked (0.70 weight) |
| Apple Newsroom | Supplemental | 2 | Down-ranked (0.70 weight) |

---

## Ingestion Rules

### Per-Feed Caps

- Maximum **3-4 articles per feed per day** (configurable per feed)
- Premium feeds get slightly higher effective caps (4)
- Standard/Supplemental feeds capped at 3

### Deduplication

1. **Source URL deduplication** - Same URL never ingested twice
2. **Dedupe key** - Title + source hash prevents near-duplicates
3. **Clustering** - Similar stories across feeds are grouped

### Topic Suppression

- Same entity/topic suppressed within 24h window
- Prevents multiple feeds covering same story independently

### Primary Source Down-Ranking

- Official company blogs (OpenAI, Google, Microsoft, Apple) are automatically down-ranked
- Base quality weight set to 0.70 (vs 0.85-0.95 for news)
- Prefer news coverage that provides context and analysis

---

## Quality Scoring Formula

For each article:

```
quality_score = base_quality_weight × quality_tier_modifier
```

Where:
- `base_quality_weight` comes from FeedConfig (0.70-0.95)
- `quality_tier_modifier` is 0.85 / 1.0 / 1.15

---

## Graceful Degradation

If some roles are sparse (few articles available):

1. System continues with available content
2. No role is "required" - quotas are targets, not minimums
3. Per-feed caps prevent any single source from dominating
4. Logging tracks distribution for monitoring

---

## Configuration

### Adding a New Feed

1. Add to `FEED_REGISTRY` in `app/integrations/rss_feeds.py`
2. Set appropriate role, quality tier, daily cap
3. Update `SOURCE_QUALITY_WEIGHTS` in `app/config/scoring.py` (optional fallback)

### Adjusting Quotas

Edit `ROLE_QUOTAS` in `rss_feeds.py`:

```python
ROLE_QUOTAS: Dict[FeedRole, int] = {
    FeedRole.BREAKING: 12,
    FeedRole.ANALYSIS: 8,
    # ...
}
```

### Environment Variables

- `DAILY_TARGET_ARTICLES` - Total articles per day (default: 30)
- `RSS_ENTRIES_PER_FEED` - Max entries to fetch per feed (default: 50)

---

## Monitoring

### Verification Checklist

- [ ] ~35-40 unique articles/day
- [ ] Balanced role distribution (check logs)
- [ ] No single feed > 15% of daily content
- [ ] Premium feeds should dominate top rankings

### Log Messages

```
Total RSS entries fetched: 150
  breaking: 45 entries
  analysis: 30 entries
  infra: 25 entries
  ...
Articles ingested by feed:
  TechCrunch: 4
  The Verge: 4
  Wired: 3
  ...
```

---

## Architecture

```
rss_feeds.py          # Feed configuration & registry
    ↓
rss_client.py         # Fetch & parse with role metadata
    ↓
ingestion/service.py  # Per-feed caps, quality modifiers
    ↓
ranking/quality.py    # Quality score computation
    ↓
config/scoring.py     # Fallback quality weights
```
