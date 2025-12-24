# Configuration Guide

This document explains how to configure the Blips backend.

## Environment Variables

All configuration is done via environment variables. Create a `.env` file:

```bash
cp .env.example .env
```

## Core Settings

### Database

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/blips
```

### Redis

```bash
REDIS_URL=redis://localhost:6379/0
```

### OpenAI

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

## Scoring Configuration

The global scoring formula is:

```
global_score = quality_weight * quality
             + trend_weight * trend
             + recency_weight * recency
             + diversity_weight * diversity
```

### Score Weights

| Variable | Default | Description |
|----------|---------|-------------|
| `SCORING_WEIGHT_QUALITY` | 0.40 | Weight for source quality |
| `SCORING_WEIGHT_TREND` | 0.30 | Weight for trending signals |
| `SCORING_WEIGHT_RECENCY` | 0.20 | Weight for freshness |
| `SCORING_WEIGHT_DIVERSITY` | 0.10 | Weight for diversity boost |

**Note**: Weights must sum to 1.0.

### Recency Decay

Content freshness uses exponential decay:

```
recency_score = 2^(-age_hours / half_life)
```

| Variable | Default | Description |
|----------|---------|-------------|
| `RECENCY_HALF_LIFE_HOURS` | 24 | Hours for score to halve |
| `RECENCY_MAX_AGE_HOURS` | 168 | Maximum age (7 days) |

Example scores with 24h half-life:
- 0 hours: 1.0
- 24 hours: 0.5
- 48 hours: 0.25
- 72 hours: 0.125

### Diversity Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DIVERSITY_MAX_TOPIC_DOMINANCE` | 0.40 | Max share for any topic |
| `DIVERSITY_BOOST_THRESHOLD` | 0.10 | Threshold for boost |
| `DIVERSITY_MAX_BOOST` | 0.50 | Maximum boost value |
| `DIVERSITY_MAX_PENALTY` | 0.50 | Maximum penalty value |

### Trend Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TREND_CLUSTER_WEIGHT` | 0.80 | Weight for cluster size |
| `TREND_ENGAGEMENT_WEIGHT` | 0.20 | Weight for engagement |

**Note**: Engagement weight is intentionally low to prevent gaming.

## Clustering Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CLUSTER_WINDOW_HOURS` | 48 | Time window for clustering |
| `CLUSTER_COMBINED_THRESHOLD` | 0.50 | Min score to cluster |
| `CLUSTER_ENTITY_WEIGHT` | 0.40 | Weight for entity overlap |
| `CLUSTER_TITLE_WEIGHT` | 0.40 | Weight for title similarity |
| `CLUSTER_TOPIC_WEIGHT` | 0.20 | Weight for topic overlap |

## Quota Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_SUMMARY_QUOTA` | 100 | Max summaries per day |
| `DAILY_CHAT_QUOTA` | 50 | Max chat messages per day |

## Cache Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `FEED_CACHE_TTL` | 300 | Feed cache duration (5 min) |
| `CONTENT_CACHE_TTL` | 600 | Content cache duration (10 min) |

## Scheduler Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULER_ENABLED` | true | Enable background jobs |
| `SCHEDULER_TIMEZONE` | UTC | Timezone for scheduling |

## Adding New Configuration

1. Add to appropriate config class in `app/config/`:

```python
# app/config/scoring.py
class ScoringWeights(BaseSettings):
    my_new_weight: float = Field(
        default=0.5,
        ge=0.0, le=1.0,
        description="My new weight"
    )
    
    class Config:
        env_prefix = "SCORING_WEIGHT_"
```

2. Use in code:

```python
from app.config.scoring import scoring_weights

value = scoring_weights.my_new_weight
```

3. Add to `.env.example`:

```bash
# My new weight (0.0-1.0)
SCORING_WEIGHT_MY_NEW_WEIGHT=0.5
```

## Source Quality Weights

Source quality weights are defined in `app/config/scoring.py`:

| Source | Weight |
|--------|--------|
| MIT Technology Review | 0.95 |
| OpenAI Blog | 0.95 |
| Anthropic Blog | 0.95 |
| TechCrunch | 0.90 |
| The Verge | 0.90 |
| Ars Technica | 0.90 |
| MKBHD | 0.90 |
| Wired | 0.85 |
| VentureBeat | 0.80 |
| CNET | 0.75 |
| Engadget | 0.75 |
| Unknown | 0.50 |

To add or modify:

```python
# app/config/scoring.py
SOURCE_QUALITY_WEIGHTS["my_source"] = 0.85
```

## Tech Topics and Entities

Topics and entities for extraction are in `app/config/content.py`.

To add new topics:

```python
# app/config/content.py
TECH_TOPICS.add("new_topic")
TECH_ENTITIES.add("New Company")
```
