# Blips AI News

An AI-powered tech news aggregator with a mobile-first experience. Fetches articles from RSS feeds and videos from YouTube channels, processes them with AI summarization, and serves them via API.

## Overview

Blips aggregates tech news from multiple sources (RSS feeds, YouTube channels), clusters similar content to avoid duplicates, ranks items by quality and relevance, and serves them to a mobile app with a rolling freshness strategy.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  RSS Feeds  │────▶│             │────▶│   Mobile    │
│  YouTube    │     │   Backend   │     │     App     │
│  Channels   │     │   (FastAPI) │     │  (Flutter)  │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
               PostgreSQL      Redis
                (data)        (cache)
```

## Features

- 📰 **Multi-source ingestion** - RSS feeds + YouTube channels
- 🤖 **AI Summarization** - GPT-powered article summaries
- 🎯 **Smart Ranking** - Quality, recency, trend, and diversity factors
- 🔗 **Content Clustering** - Deduplication of similar stories
- 💬 **AI Chat** - Ask questions about any article
- 📊 **Engagement Tracking** - Learn from user interactions
- ⏰ **Background Jobs** - Automatic content refresh
- 🔄 **Rolling Freshness** - Tiered A/B/C content strategy with auto top-up
- 📈 **Inventory Health** - Self-healing content reservoir

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI (Python 3.11+) |
| Database | PostgreSQL + SQLAlchemy |
| Cache | Redis |
| AI | OpenAI GPT-5 nano / Mistral |
| Scheduler | APScheduler |
| Container | Docker + Docker Compose |

## Quick Start

```bash
# From the repository root
cp src/backend/.env.example src/backend/.env
# Edit .env and add OPENAI_API_KEY

# Start services
docker compose -f src/docker-compose.yml up -d

# Run migrations
docker compose -f src/docker-compose.yml exec api alembic upgrade head

# Verify
curl http://localhost:8000/health
```

## API Documentation

Interactive API documentation is available at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/api/v1/openapi.json

## API Endpoints

### Content Feeds
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/articles/recent` | Get recent articles with tiered freshness |
| GET | `/api/v1/articles/{id}` | Get article by ID with conversation |
| GET | `/api/v1/videos/recent` | Get recent videos |
| GET | `/api/v1/videos/reels` | Get short-form videos for reels player |

### Session & Personalization
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/session/playlist` | Personalized mixed feed |
| POST | `/api/v1/session/interactions` | Log user engagement events |

### Health & Monitoring
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic health check |
| GET | `/metrics` | Ingestion metrics and budget status |
| GET | `/api/v1/inventory/health` | Content inventory health across tiers |

### AI Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/ai/respond` | AI chat about content |
| GET | `/api/v1/conversations/{id}` | Get conversation history |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/flags` | Get feature flags |
| POST | `/api/v1/admin/trigger-fetch` | Trigger manual ingestion |
| GET | `/api/v1/admin/stats/content` | Content statistics |

## Project Structure

```
src/
├── Dockerfile
├── docker-compose.yml
└── backend/
    ├── app/
    │   ├── api/routes/          # HTTP endpoints
    │   ├── models/              # SQLAlchemy models
    │   ├── repositories/        # Data access layer
    │   ├── services/            # Business logic
    │   │   ├── inventory_service.py   # Health monitoring
    │   │   ├── tiered_feed_service.py # Freshness tiers
    │   │   ├── topup_service.py       # Auto ingestion
    │   │   ├── playlist_service.py    # Personalized feeds
    │   │   └── diversity_mixer.py     # Feed balancing
    │   ├── ingestion/           # Content fetching
    │   ├── clustering/          # Content deduplication
    │   ├── ranking/             # Item scoring
    │   ├── scheduler/           # Background jobs
    │   └── core/                # Config, settings
    ├── alembic/                 # Database migrations
    └── tests/                   # Unit tests
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System design and data flow |
| [Content Sources](docs/CONTENT_SOURCES.md) | Active RSS feeds and YouTube channels |
| [Configuration](docs/CONFIGURATION.md) | Environment variables and tuning |
| [Development Guide](docs/DEVELOPMENT.md) | Local setup, testing, debugging |
| [Feed Freshness Strategy](docs/FEED_FRESHNESS_STRATEGY.md) | Tiered content delivery |
| [Ingestion Guide](docs/INGESTION.md) | Ingestion pipeline and observability |
| [Operations](docs/OPERATIONS.md) | Monitoring, incidents, and runbooks |

## Development

```bash
# View logs
docker compose -f src/docker-compose.yml logs -f api

# Run tests
docker compose -f src/docker-compose.yml exec api pytest

# Run specific tests
docker compose -f src/docker-compose.yml exec api pytest tests/unit/test_tiered_feed.py -v

# Database shell
docker compose -f src/docker-compose.yml exec db psql -U postgres -d blips

# Generate migration
docker compose -f src/docker-compose.yml exec api alembic revision --autogenerate -m "description"
```

## Related Projects

- [blips-mobile](../blips-mobile) - Flutter mobile app

## License

MIT
