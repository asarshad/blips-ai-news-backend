# Backend Architecture

This document describes the architecture of the Blips AI News Backend.

## Overview

The backend is a Python/FastAPI application that:
1. Ingests content from RSS feeds and YouTube
2. Processes and scores content using ML-assisted ranking
3. Serves personalized feeds to the mobile app

## Domain Structure

The codebase is organized into domain-driven modules:

```
app/
├── api/              # FastAPI route handlers (thin controllers)
├── config/           # Centralized configuration (no magic numbers)
├── core/             # Shared utilities (logging, exceptions)
├── db/               # Database session management
├── models/           # SQLAlchemy ORM models
├── repositories/     # Data access layer (persistence)
├── scheduler/        # APScheduler background jobs
├── services/         # Legacy services (being refactored)
│
├── clustering/       # Content clustering domain
├── ingestion/        # Content ingestion domain
└── ranking/          # Content scoring domain
```

## Key Domains

### Config (`app/config/`)

Centralized configuration with Pydantic settings. All tunable parameters
live here - no magic numbers in business logic.

| Module | Purpose |
|--------|---------|
| `settings.py` | Core settings (DB, Redis, OpenAI) |
| `scoring.py` | Ranking weights and parameters |
| `clustering.py` | Clustering thresholds |
| `content.py` | Tech topics/entities lists |
| `feeds.py` | RSS feed sources |
| `youtube.py` | YouTube channel configs |

### Ranking (`app/ranking/`)

Implements the global scoring algorithm:

```
global_score = 0.40 * quality + 0.30 * trend + 0.20 * recency + 0.10 * diversity
```

| Module | Purpose |
|--------|---------|
| `quality.py` | Source quality + content completeness |
| `recency.py` | Exponential decay from publish time |
| `trend.py` | Cluster size + engagement signals |
| `diversity.py` | Topic dominance penalty/boost |
| `global_score.py` | Combined score computation |
| `service.py` | Scoring job orchestration |

### Clustering (`app/clustering/`)

Groups related content to prevent repetition.

| Module | Purpose |
|--------|---------|
| `similarity.py` | Entity/title/topic overlap computation |
| `dedupe.py` | Deduplication key generation |
| `service.py` | Clustering job orchestration |

### Ingestion (`app/ingestion/`)

Converts raw articles/videos into unified content items.

| Module | Purpose |
|--------|---------|
| `extractors.py` | Topic/entity/source extraction |
| `service.py` | Ingestion pipeline orchestration |
| `checkpointing.py` | Checkpoint-based ingestion with budget tracking |

### Services (`app/services/`)

Business logic and feed generation.

| Module | Purpose |
|--------|---------|
| `inventory_service.py` | Health monitoring with tier counts |
| `tiered_feed_service.py` | A/B/C freshness tier blending |
| `topup_service.py` | Auto-ingestion when inventory low |
| `playlist_service.py` | Personalized feed generation |
| `diversity_mixer.py` | Topic/source diversity balancing |
| `personalization_service.py` | User preference learning |

## Data Flow

```
RSS Feeds ─────┐
               ├──► Ingestion ──► Scoring ──► Tiered Feed ──► API
YouTube ───────┘        │              │
                        ▼              ▼
                   Clustering    Inventory Health
                                       │
                                       ▼
                                 Auto Top-Up
```

1. **Fetch**: Scheduler pulls from RSS/YouTube with budget limits
2. **Ingest**: Normalize into `content_items`, extract metadata
3. **Cluster**: Group related stories
4. **Score**: Compute quality/trend/recency/diversity
5. **Tier**: Classify content into Fresh (A), Backfill (B), Evergreen (C)
6. **Serve**: API returns tiered, diverse, personalized content
7. **Monitor**: Inventory health triggers top-up when needed

## Database Schema

Primary tables:
- `content_items` - Unified content with scores and freshness
- `ingestion_progress` - Per-feed checkpoint tracking
- `ingestion_budget` - Daily target quotas
- `user_profiles` - User settings
- `interaction_events` - Engagement tracking

## Background Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `fetch_and_process_news` | 30 min | Pull RSS/YouTube with checkpoints |
| `run_scoring` | 1 hour | Update global scores |
| `run_clustering` | 15 min | Cluster related content |
| `preference_decay` | Daily | Decay old preferences |
| `retry_ai_processing` | 2 hours | Retry failed AI extractions |

## Tiered Freshness Strategy

Content is classified into tiers for balanced feeds:

| Tier | Name | Criteria | Priority |
|------|------|----------|----------|
| A | Fresh | published_at < 36h (articles) | Highest |
| B | Backfill | created_at < 24h, older publish | Medium |
| C | Evergreen | High score, < 14 days | Lowest |

See [FEED_FRESHNESS_STRATEGY.md](docs/FEED_FRESHNESS_STRATEGY.md) for details.

## Configuration

All configuration via environment variables with `.env`:

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost/blips
REDIS_URL=redis://localhost:6379/0

# Scoring weights (override defaults)
SCORING_WEIGHT_QUALITY=0.40
SCORING_WEIGHT_TREND=0.30
SCORING_WEIGHT_RECENCY=0.20
SCORING_WEIGHT_DIVERSITY=0.10

# Freshness windows
ARTICLES_FRESH_PUBLISHED_HOURS=36
ARTICLES_BACKFILL_CREATED_HOURS=24
MIN_FRESH_ARTICLES=30
RESERVOIR_ARTICLES=200
```

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/articles/recent` | GET | Tiered article feed |
| `/api/v1/videos/recent` | GET | Video feed |
| `/api/v1/videos/reels` | GET | Short-form videos |
| `/api/v1/session/playlist` | GET | Personalized mixed feed |
| `/api/v1/inventory/health` | GET | Inventory health by tier |
| `/api/v1/ai/respond` | POST | AI chat about content |
| `/health` | GET | Health check |
| `/metrics` | GET | Ingestion metrics |

## API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Testing

```bash
cd src/backend
pytest tests/
pytest tests/unit/test_tiered_feed.py -v
pytest tests/unit/test_inventory_service.py -v
```

## Running

```bash
# Development
docker-compose up -d
uvicorn app.main:app --reload

# Production
docker build -t blips-backend .
docker run -p 8000:8000 blips-backend
```
