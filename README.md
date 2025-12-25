# Blips AI News

An AI-powered tech news aggregator with a mobile-first experience. Fetches articles from RSS feeds and videos from YouTube channels, processes them with AI summarization, and serves them via API.

## Overview

Blips aggregates tech news from multiple sources (RSS feeds, YouTube channels), clusters similar content to avoid duplicates, ranks items by quality and relevance, and serves them to a mobile app.

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

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI (Python 3.11+) |
| Database | PostgreSQL + SQLAlchemy |
| Cache | Redis |
| AI | OpenAI GPT-4 |
| Scheduler | APScheduler |
| Container | Docker + Docker Compose |

## Quick Start

```bash
cd src/backend

# Create environment file
cp .env.example .env
# Edit .env and add OPENAI_API_KEY

# Start services
docker-compose up -d

# Run migrations
docker-compose exec api alembic upgrade head

# Verify
curl http://localhost:8000/api/v1/health
```

## Project Structure

```
src/backend/
├── app/
│   ├── api/routes/          # HTTP endpoints
│   ├── models/              # SQLAlchemy models
│   ├── repositories/        # Data access layer
│   ├── services/            # Business logic
│   │   ├── clustering.py    # Content deduplication
│   │   ├── ranking.py       # Item scoring
│   │   └── summarization.py # AI summaries
│   ├── scheduler/           # Background jobs
│   └── core/                # Config, settings
├── alembic/                 # Database migrations
└── docker-compose.yml
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/articles/recent` | Get recent articles |
| GET | `/api/v1/videos/recent` | Get recent videos |
| GET | `/api/v1/videos/reels` | Get videos for reels player |
| POST | `/api/v1/interactions` | Track user engagement |
| POST | `/api/v1/ai/chat` | AI chat about content |

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System design and data flow |
| [Backend Structure](docs/BACKEND_STRUCTURE.md) | Code organization and patterns |
| [Configuration](docs/CONFIGURATION.md) | Environment variables and tuning |
| [Development Guide](docs/DEVELOPMENT_GUIDE.md) | Local setup, testing, debugging |

## Development

```bash
# View logs
docker-compose logs -f api

# Run tests
docker-compose exec api pytest

# Database shell
docker-compose exec db psql -U postgres -d blips

# Generate migration
docker-compose exec api alembic revision --autogenerate -m "description"
```

## Related Projects

- [blips-mobile](../blips-mobile) - Flutter mobile app

## License

MIT
