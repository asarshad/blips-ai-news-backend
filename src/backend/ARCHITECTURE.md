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

## Data Flow

```
RSS Feeds ─────┐
               ├──► Ingestion ──► Scoring ──► Feed API
YouTube ───────┘        │
                        ▼
                   Clustering
```

1. **Fetch**: Scheduler pulls from RSS/YouTube
2. **Ingest**: Normalize into `content_items`, extract metadata
3. **Cluster**: Group related stories
4. **Score**: Compute quality/trend/recency/diversity
5. **Serve**: API returns personalized, ranked content

## Database Schema

Primary tables:
- `content_items` - Unified content with scores
- `articles` - Raw article data
- `videos` - Raw video data
- `user_profiles` - User settings
- `interaction_events` - Engagement tracking

## Background Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `fetch_articles` | 15 min | Pull RSS feeds |
| `fetch_videos` | 30 min | Pull YouTube |
| `run_scoring` | 1 hour | Update scores |
| `run_clustering` | 15 min | Cluster content |
| `preference_decay` | Daily | Decay old preferences |

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

# Recency decay
RECENCY_HALF_LIFE_HOURS=24
RECENCY_MAX_AGE_HOURS=168
```

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/feed/articles` | GET | Personalized article feed |
| `/api/feed/videos` | GET | Personalized video feed |
| `/api/feed/reels` | GET | Short-form videos |
| `/api/chat` | POST | AI chat about content |
| `/api/events` | POST | Track engagement |

## Testing

```bash
cd src/backend
pytest tests/
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
