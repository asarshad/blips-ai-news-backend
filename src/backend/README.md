
# Blips AI News Backend

A FastAPI backend for an AI-powered tech news application. This backend fetches, summarizes, and serves tech news articles with AI-powered chat capabilities.

## Features

- FastAPI REST API
- PostgreSQL database with SQLAlchemy ORM
- Redis caching for articles
- OpenAI GPT integration for article summarization and chat
- Background task scheduling with APScheduler
- Docker containerization
- Quota management system

## Architecture

The backend follows a clean, modular architecture:

- **API Layer**: FastAPI routes and endpoints
- **Service Layer**: Business logic and integrations
- **Data Layer**: SQLAlchemy models and database access
- **Scheduler**: Background tasks for news fetching and processing

## Setup and Installation

### Prerequisites

- Docker and Docker Compose
- OpenAI API key

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
5. The API will be available at http://localhost:8000
6. API documentation: http://localhost:8000/docs

### Database Migrations

Migrations are managed with Alembic:

```bash
# Inside the api container
docker exec -it blips-api-1 bash
cd /app
alembic upgrade head
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | /api/v1/articles/next | Fetch next article |
| GET    | /api/v1/articles/{id} | Fetch article with conversation |
| GET    | /api/v1/articles/cache | Get cached articles |
| POST   | /api/v1/conversations/{article_id} | Save message |
| GET    | /api/v1/conversations/{article_id} | Get past messages |
| POST   | /api/v1/ai/respond | Send user message, get GPT reply |
| GET    | /api/v1/usage | Return remaining quota per device |

## Development

### Project Structure

```
backend/
├── app/
│   ├── main.py                 # Application entry point
│   ├── api/                    # API routes
│   │   └── routes/             # Endpoint definitions
│   ├── models/                 # SQLAlchemy models
│   ├── db/                     # Database setup
│   ├── services/               # Business logic
│   ├── scheduler/              # Background tasks
│   └── config.py               # Configuration
├── alembic/                    # Database migrations
└── requirements.txt            # Python dependencies
```

### Adding New Features

1. Create/modify SQLAlchemy models in `app/models/`
2. Generate migration with Alembic: `alembic revision --autogenerate -m "description"`
3. Implement business logic in `app/services/`
4. Add API endpoints in `app/api/routes/`

## Configuration

All configuration is managed through environment variables or the `.env` file:

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `OPENAI_API_KEY`: Your OpenAI API key
- `MAX_MESSAGES_PER_DAY`: Daily message quota per device
- `MAX_MESSAGES_PER_ARTICLE`: Message quota per article per device
- `NEWS_FETCH_INTERVAL_MINUTES`: How often to fetch new articles
- `ARTICLE_CACHE_COUNT`: Number of articles to cache

## License

MIT
