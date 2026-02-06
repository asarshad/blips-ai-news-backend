
# Blips AI News Backend

A FastAPI backend for an AI-powered tech news application. This backend fetches, summarizes, and serves tech news articles and videos with AI-powered chat capabilities and tiered freshness strategy.

## Features

- FastAPI REST API with Swagger UI
- PostgreSQL database with SQLAlchemy ORM
- Redis caching for feeds and playlists
- OpenAI/Mistral integration for article summarization and chat
- Background task scheduling with APScheduler
- Docker containerization
- Rolling freshness strategy with tiered content (A/B/C)
- Self-healing inventory with auto top-up

## Architecture

The backend follows a clean, modular architecture:

- **API Layer**: FastAPI routes and endpoints
- **Service Layer**: Business logic (tiered feeds, inventory health, personalization)
- **Data Layer**: SQLAlchemy models and repositories
- **Scheduler**: Background tasks for news fetching and processing
- **Ingestion**: Checkpoint-based content fetching with budget tracking

## Setup and Installation

### Prerequisites

- Docker and Docker Compose
- OpenAI API key (or Mistral API key)

### Getting Started

1. Clone the repository
2. Create a `.env` file from `.env.example`:
   ```
   cp .env.example .env
   ```
3. Add your OpenAI API key to the `.env` file
4. Build and start the containers:
   ```
   docker-compose up -d
   ```
5. Run database migrations:
   ```
   docker-compose exec api alembic upgrade head
   ```
6. The API will be available at http://localhost:8000

### API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/api/v1/openapi.json

## API Endpoints

### Content Feeds
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/articles/recent` | Fetch recent articles with freshness tiers |
| GET | `/api/v1/articles/{id}` | Fetch article with conversation |
| GET | `/api/v1/videos/recent` | Fetch recent videos |
| GET | `/api/v1/videos/reels` | Fetch short-form videos |

### Session & Personalization
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/session/playlist` | Get personalized feed |
| POST | `/api/v1/session/interactions` | Log user events |
| GET | `/api/v1/session/preferences` | Get user preferences |

### Health & Monitoring
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/metrics` | Ingestion metrics |
| GET | `/api/v1/inventory/health` | Inventory health by tier |

### AI Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/ai/respond` | AI chat response |
| GET | `/api/v1/conversations/{id}` | Get conversation history |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/flags` | Feature flags |
| POST | `/api/v1/admin/trigger-fetch` | Trigger ingestion |
| GET | `/api/v1/admin/stats/content` | Content stats |

## Development

### Project Structure

```
backend/
├── app/
│   ├── main.py                 # Application entry point
│   ├── api/                    # API routes
│   │   └── routes/             # Endpoint handlers
│   ├── models/                 # SQLAlchemy models
│   ├── db/                     # Database setup
│   ├── services/               # Business logic
│   │   ├── inventory_service.py   # Health monitoring
│   │   ├── tiered_feed_service.py # Freshness tiers
│   │   ├── topup_service.py       # Auto ingestion
│   │   ├── playlist_service.py    # Personalized feeds
│   │   └── diversity_mixer.py     # Feed balancing
│   ├── ingestion/              # Content fetching
│   ├── clustering/             # Content deduplication
│   ├── ranking/                # Score calculation
│   ├── scheduler/              # Background tasks
│   ├── repositories/           # Data access
│   └── core/                   # Config, logging
├── alembic/                    # Database migrations
├── tests/                      # Unit tests
├── docs/                       # Documentation
└── requirements.txt            # Python dependencies
```

### Running Tests

```bash
# Inside the api container
docker-compose exec api pytest

# Run specific test files
docker-compose exec api pytest tests/unit/test_tiered_feed.py -v
docker-compose exec api pytest tests/unit/test_inventory_service.py -v
```

### Database Migrations

```bash
# Inside the api container
docker-compose exec api alembic upgrade head
docker-compose exec api alembic revision --autogenerate -m "description"
```

### Adding New Features

1. Create/modify SQLAlchemy models in `app/models/`
2. Generate migration with Alembic: `alembic revision --autogenerate -m "description"`
3. Implement business logic in `app/services/`
4. Add API endpoints in `app/api/routes/`
5. Add tests in `tests/`

## Configuration

All configuration is managed through environment variables or the `.env` file:

### Required
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `OPENAI_API_KEY`: Your OpenAI API key (or `MISTRAL_API_KEY`)

### Optional
- `LLM_PROVIDER`: "openai" (default) or "mistral"
- `MAX_MESSAGES_PER_DAY`: Daily message quota per device (default: 5)
- `MAX_MESSAGES_PER_ARTICLE`: Message quota per article (default: 3)
- `NEWS_FETCH_INTERVAL_MINUTES`: Feed refresh interval (default: 30)

### Freshness Strategy
- `ARTICLES_FRESH_PUBLISHED_HOURS`: Fresh tier window (default: 36)
- `MIN_FRESH_ARTICLES`: Minimum fresh items threshold (default: 30)
- `RESERVOIR_ARTICLES`: Total inventory size (default: 200)

See [CONFIGURATION.md](CONFIGURATION.md) for complete reference.

## License

MIT
