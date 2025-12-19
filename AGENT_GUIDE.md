# Blips AI News - Backend Agent Guide

## Project Overview
**Blips Backend** is a FastAPI-powered REST API that fetches tech news articles from RSS feeds and YouTube videos, processes them with AI summarization, and serves them to the mobile frontend. It includes AI-powered chat capabilities using OpenAI GPT.

### Key Features
- 📡 Automated RSS feed fetching from tech sites
- 🎥 YouTube video integration
- 🤖 AI-powered article summarization (OpenAI)
- 💬 AI chat about articles with conversation history
- 📊 Usage quotas (5 msgs/day, 3 msgs/article)
- ⚡ Redis caching for performance
- 🔄 Background scheduler for periodic updates
- 🗄️ PostgreSQL database with SQLAlchemy ORM

---

## Tech Stack

### Core Framework
- **FastAPI** - Modern async web framework
- **Python 3.9+** - Programming language
- **Uvicorn** - ASGI server
- **Gunicorn** - Production WSGI server

### Database & ORM
- **PostgreSQL 14** - Relational database
- **SQLAlchemy 2.0** - ORM with async support
- **Alembic** - Database migrations

### Caching
- **Redis 7** - In-memory cache

### Background Jobs
- **APScheduler** - Task scheduling

### AI/ML
- **OpenAI API** - GPT models for chat & summarization

### Content Processing
- **feedparser** - RSS/Atom feed parsing
- **BeautifulSoup4** - HTML parsing and cleaning
- **requests** - HTTP client

### Utilities
- **python-dotenv** - Environment variables
- **Pydantic** - Data validation
- **loguru** - Logging
- **Pillow (PIL)** - Image processing for proxy endpoint

---

## Infrastructure Setup

### Running the Backend

**This project runs via Docker Compose - NO virtual environment (venv) needed.**

#### Prerequisites
- Docker Desktop installed
- Docker Compose v2+

#### Quick Start

```bash
# Navigate to source directory
cd /path/to/blips-ai-news-backend/src

# Start all services (API, PostgreSQL, Redis)
docker compose up -d

# View logs
docker compose logs -f api

# Stop services
docker compose down
```

#### Container Services

1. **api** - FastAPI application (port 8000)
   - Built from `Dockerfile` with Python 3.11-slim
   - Runs with Gunicorn + 4 Uvicorn workers
   - Auto-restarts on failure
   - Health checks every 30s

2. **db** - PostgreSQL 14 (port 5432)
   - Persistent volume for data
   - Database: `blips_news`

3. **redis** - Redis 7 (port 6379)
   - In-memory cache
   - No persistence (cache only)

#### Making Code Changes

```bash
# After modifying Python code or requirements.txt
cd /path/to/blips-ai-news-backend/src

# Rebuild and restart API container
docker compose build api
docker compose up -d

# Verify changes
docker compose logs -f api
```

#### Database Migrations

```bash
# Run migrations inside container
docker compose exec api alembic upgrade head

# Create new migration
docker compose exec api alembic revision --autogenerate -m "description"
```

#### Verifying Installation

```bash
# Check container status
docker compose ps

# Test API health
curl http://localhost:8000/api/v1/health

# Verify Pillow (for image proxy)
docker compose exec api python -c "from PIL import Image; print(f'Pillow: {Image.__version__}')"
```

#### Environment Variables

Create `.env` file in `src/` directory:

```bash
# Database
DATABASE_URL=postgresql://postgres:postgres@db:5432/blips_news

# Redis
REDIS_URL=redis://redis:6379/0

# OpenAI
OPENAI_API_KEY=sk-...

# App Settings
CORS_ORIGINS=["http://localhost:5173","capacitor://localhost","ionic://localhost"]
```

#### Troubleshooting

**Container won't start:**
```bash
docker compose logs api
docker compose restart api
```

**Database connection errors:**
```bash
# Check if db container is healthy
docker compose ps
# Wait 10s for PostgreSQL to fully initialize
```

**Port conflicts:**
```bash
# Check what's using port 8000
lsof -i :8000
# Edit docker-compose.yml to use different port
```

### Development Without Docker (Optional)

**Not recommended** - If you need local Python environment for IDE autocomplete:

```bash
# Create venv (for IDE only, not for running)
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# Run locally (requires PostgreSQL + Redis running)
cd backend
uvicorn app.main:app --reload
```

**Note:** The venv is in `.gitignore` and should NOT be committed.

---

## Project Structure

```
src/backend/
├── app/
│   ├── main.py                 # FastAPI app entry point
│   │
│   ├── api/                    # API routes
│   │   ├── __init__.py             # API router registration
│   │   └── routes/
│   │       ├── articles.py         # Article endpoints
│   │       ├── videos.py           # Video endpoints
│   │       ├── chat.py             # AI chat endpoints
│   │       └── usage.py            # Quota tracking endpoints
│   │
│   ├── core/                   # Core utilities
│   │   ├── config.py               # App configuration (Pydantic settings)
│   │   ├── dependencies.py         # FastAPI dependencies (DB, Redis)
│   │   ├── logging.py              # Logging setup
│   │   └── exceptions.py           # Custom exceptions
│   │
│   ├── db/                     # Database setup
│   │   ├── base.py                 # SQLAlchemy Base and engine
│   │   └── session.py              # Database session management
│   │
│   ├── models/                 # SQLAlchemy models
│   │   ├── article.py              # Article & Tag models
│   │   ├── video.py                # Video model
│   │   ├── conversation.py         # Chat conversation model
│   │   └── usage.py                # Usage tracking model
│   │
│   ├── repositories/           # Data access layer (Repository pattern)
│   │   ├── article_repo.py         # Article CRUD operations
│   │   ├── video_repo.py           # Video CRUD operations
│   │   ├── conversation_repo.py    # Chat history CRUD
│   │   └── usage_repo.py           # Usage tracking CRUD
│   │
│   ├── schemas/                # Pydantic schemas (API models)
│   │   ├── article.py              # Article request/response schemas
│   │   ├── video.py                # Video request/response schemas
│   │   ├── chat.py                 # Chat request/response schemas
│   │   └── usage.py                # Usage quota schemas
│   │
│   ├── services/               # Business logic layer
│   │   ├── news_fetcher.py         # RSS feed fetching
│   │   ├── video_fetcher.py        # YouTube video fetching
│   │   ├── summarizer.py           # AI summarization
│   │   ├── article_service.py      # Article business logic
│   │   ├── video_service.py        # Video business logic
│   │   ├── ai_chat.py              # AI chat service
│   │   └── quota_manager.py        # Usage quota management
│   │
│   ├── scheduler/              # Background tasks
│   │   ├── __init__.py             # Scheduler initialization
│   │   └── tasks.py                # Scheduled tasks (fetch articles/videos)
│   │
│   └── integrations/           # External service clients
│       └── openai_client.py        # OpenAI API wrapper
│
├── alembic/                    # Database migrations
│   ├── versions/                   # Migration scripts
│   ├── env.py                      # Alembic environment
│   └── script.py.mako              # Migration template
│
├── alembic.ini                 # Alembic configuration
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (not in git)
└── README.md                   # Project documentation
```

---

## Architecture Patterns

### 1. Layered Architecture
```
API Layer (routes/)
    ↓
Service Layer (services/)
    ↓
Repository Layer (repositories/)
    ↓
Model Layer (models/)
    ↓
Database
```

### 2. Repository Pattern
All database operations go through repositories:
- **ArticleRepository** - Article CRUD
- **VideoRepository** - Video CRUD
- **ConversationRepository** - Chat history
- **UsageRepository** - Quota tracking

**Benefits:**
- Testable (mock repositories)
- Centralized data access
- Consistent error handling

### 3. Dependency Injection
FastAPI's dependency system provides:
- Database sessions
- Redis clients
- Service instances

```python
def get_article_service(
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis)
) -> ArticleService:
    article_repo = ArticleRepository(db)
    return ArticleService(article_repo, redis_client)
```

---

## Database Schema

### Articles Table
```python
articles:
  - id: Integer (PK)
  - title: String (indexed)
  - source_url: String (unique, indexed)
  - content: Text
  - summary: Text
  - image_url: String (nullable)
  - published_date: Date (indexed)
  - created_at: DateTime
  - hot_score: Integer (indexed)
  - read_time_minutes: Integer
```

### Videos Table
```python
videos:
  - id: Integer (PK)
  - title: String
  - video_url: String (unique)
  - source_url: String
  - thumbnail_url: String (nullable)
  - summary: Text
  - source: String
  - category: String
  - duration_seconds: Integer (nullable)
  - created_at: DateTime
```

### Conversations Table
```python
conversations:
  - id: Integer (PK)
  - article_id: Integer (FK -> articles.id)
  - message: Text
  - sender: Enum('user', 'ai')
  - timestamp: DateTime
```

### Tags Table
```python
tags:
  - id: Integer (PK)
  - name: String (unique, indexed)

article_tag (association table):
  - article_id: Integer (FK -> articles.id)
  - tag_id: Integer (FK -> tags.id)
```

### Usage Table
```python
usage:
  - id: Integer (PK)
  - user_id: String (nullable, indexed)
  - article_id: Integer (nullable, FK)
  - message_count: Integer
  - date: Date (indexed)
```

---

## Key API Endpoints

### Articles
```
GET  /api/v1/articles/recent?limit=100
     Returns most recent articles ordered by published_date DESC
     Response: { articles: Article[] }

GET  /api/v1/articles/next?current_id=123
     Get next article after current_id
     Response: Article

GET  /api/v1/articles/cache
     Get pre-cached articles (5 most recent)
     Response: Article[]

GET  /api/v1/articles/tags/{tag_name}?limit=10
     Get articles by tag
     Response: { articles: Article[] }

GET  /api/v1/articles/tags?limit=10
     Get popular tags with counts
     Response: TagCount[]

POST /api/v1/articles/fetch
     Manually trigger article fetching
     Response: { status, message }
```

### Videos
```
GET  /api/v1/videos/recent?limit=50
     Returns most recent videos ordered by created_at DESC
     Response: { videos: Video[] }

POST /api/v1/videos/fetch
     Manually trigger video fetching
     Response: { status, message }
```

### Chat
```
POST /api/v1/chat/{article_id}
     Send message and get AI response
     Body: { message: string }
     Headers: X-OpenAI-Key (optional)
     Response: { 
       response: string,
       remaining_daily: int,
       remaining_article: int | null
     }

GET  /api/v1/chat/{article_id}/history
     Get conversation history for article
     Response: {
       article_id: int,
       conversations: Conversation[]
     }
```

### Usage
```
GET  /api/v1/usage/stats/{article_id}
     Get remaining quota for user
     Response: {
       remaining_daily_messages: int,
       remaining_article_messages: int | null
     }
```

---

## Core Services

### 1. NewsFetcher (news_fetcher.py)
**Purpose:** Fetch articles from RSS feeds.

**Key Methods:**
```python
fetch_latest_articles() -> List[Dict]:
    # Fetches from all RSS_FEEDS in config
    # Parses published date, title, content
    # Filters out duplicates by source_url
    # Returns list of article dicts
```

**RSS Feeds:** Configured in `core/config.py`:
- TechCrunch
- The Verge
- Wired
- Ars Technica

### 2. VideoFetcher (video_fetcher.py)
**Purpose:** Fetch videos from YouTube channels.

**Key Methods:**
```python
fetch_latest_videos() -> List[Dict]:
    # Fetches from YouTube RSS feeds
    # Extracts video URL, thumbnail, duration
    # Returns list of video dicts

save_videos(videos: List[Dict]) -> int:
    # Saves videos to database
    # Skips duplicates by video_url
    # Returns count of saved videos
```

### 3. ArticleSummarizer (summarizer.py)
**Purpose:** Generate AI summaries of articles.

**Key Methods:**
```python
summarize_article(article_data: Dict) -> Dict:
    # Uses OpenAI GPT to generate summary
    # Estimates read time from content length
    # Returns enhanced article dict

save_article(article_data: Dict) -> Article:
    # Saves article to database
    # Creates tag relationships
    # Returns saved Article model
```

**Summary Prompt:**
```
Summarize the following tech article in 2-3 sentences.
Focus on key points and make it engaging for tech enthusiasts.

Article: {title}
Content: {content}
```

### 4. ArticleService (article_service.py)
**Purpose:** Business logic for articles.

**Key Methods:**
```python
get_recent_articles(limit: int) -> List[Article]:
    # Returns articles ordered by published_date DESC

get_next_article(current_id: int) -> Article:
    # Returns next article after current_id

get_cached_articles() -> List[Article]:
    # Returns 5 pre-cached articles from Redis
    
cache_articles() -> None:
    # Caches 5 most recent articles to Redis
    
get_articles_by_tag(tag_name: str, limit: int) -> List[Article]
get_popular_tags(limit: int) -> List[TagCount]
```

### 5. AiChatService (ai_chat.py)
**Purpose:** AI-powered chat about articles.

**Key Methods:**
```python
get_ai_response(
    article_id: int,
    user_message: str,
    history_limit: int = 3
) -> Dict[str, Any]:
    # Fetches article context
    # Retrieves conversation history (last 3 msgs)
    # Generates AI response via OpenAI
    # Returns response and token usage

save_conversation(
    article_id: int,
    user_message: str,
    ai_response: str
) -> Dict[str, Any]:
    # Saves user and AI messages to DB
    # Returns conversation IDs
```

**Chat System Prompt:**
```
You are a helpful AI assistant discussing this article:
Title: {title}
Summary: {summary}

Answer questions about the article. Be concise and informative.
```

### 6. QuotaManager (quota_manager.py)
**Purpose:** Track and enforce usage quotas.

**Quotas:**
- 5 messages per day (global per user)
- 3 messages per article

**Key Methods:**
```python
check_quota(user_id: str, article_id: int) -> Tuple[bool, int, int]:
    # Returns (can_send, remaining_daily, remaining_article)

increment_usage(user_id: str, article_id: int) -> None:
    # Increments usage counters

get_remaining_quota(user_id: str, article_id: int) -> Dict:
    # Returns remaining quota
```

---

## Background Scheduler

### Configuration
**Location:** `app/scheduler/__init__.py`

**Schedule:** Every 30 minutes (configurable via `NEWS_FETCH_INTERVAL_MINUTES`)

### Tasks (scheduler/tasks.py)

#### fetch_and_process_news()
**Triggered:** Every 30 minutes + on app startup

**Flow:**
1. Fetch articles from RSS feeds via `NewsFetcher`
2. For each article:
   - Generate summary via `ArticleSummarizer`
   - Extract tags
   - Save to database
3. Fetch videos from YouTube via `VideoFetcher`
4. Update article cache in Redis

**Error Handling:**
- Per-article error handling (one failure doesn't break batch)
- Separate transactions for articles and videos
- Logs all errors via loguru

---

## Caching Strategy

### Redis Cache
**Location:** Redis keys managed by `ArticleService`

**Cached Data:**
- 5 most recent articles (`articles:cache`)
- Article count (`articles:count`)

**TTL:** 5 minutes

**Usage:**
```python
# Cache articles
article_service.cache_articles()

# Retrieve cached articles
cached = article_service.get_cached_articles()
```

---

## Configuration

### Environment Variables
**Location:** `.env` file (not in git)

```bash
# Database
DATABASE_URL=postgresql://postgres:postgres@db:5432/blips

# Redis
REDIS_URL=redis://redis:6379/0

# OpenAI
OPENAI_API_KEY=sk-...

# Quotas
MAX_MESSAGES_PER_DAY=5
MAX_MESSAGES_PER_ARTICLE=3

# Scheduler
NEWS_FETCH_INTERVAL_HOURS=0
NEWS_FETCH_INTERVAL_MINUTES=30

# Cache
ARTICLE_CACHE_COUNT=5
```

### Settings Class
**Location:** `app/core/config.py`

```python
class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "blips-ai-news"
    DATABASE_URL: str
    REDIS_URL: str
    OPENAI_API_KEY: str
    RSS_FEEDS: List[str] = [...]
    MAX_MESSAGES_PER_DAY: int = 5
    MAX_MESSAGES_PER_ARTICLE: int = 3
    # ...
```

**Usage:**
```python
from app.core.config import settings

print(settings.DATABASE_URL)
```

---

## Database Migrations

### Alembic Setup
**Location:** `alembic/` directory

### Common Commands
```bash
# Create new migration
alembic revision --autogenerate -m "Add column X to table Y"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# View migration history
alembic history

# Current version
alembic current
```

### Migration Workflow
1. Modify SQLAlchemy models in `app/models/`
2. Generate migration: `alembic revision --autogenerate -m "description"`
3. Review generated migration in `alembic/versions/`
4. Apply: `alembic upgrade head`

---

## Deployment

### Docker Setup
**Location:** `src/docker-compose.yml`

**Services:**
- `api` - FastAPI app (port 8000)
- `db` - PostgreSQL 14 (port 5432)
- `redis` - Redis 7 (port 6379)

### Starting the Stack
```bash
cd src/backend
docker-compose up -d
```

### Health Checks
```bash
# API health
curl http://localhost:8000/health

# Database
docker-compose exec db pg_isready -U postgres

# Redis
docker-compose exec redis redis-cli ping
```

### Production Deployment
**Dockerfile:** `src/backend/Dockerfile`

**Entry Point:** Gunicorn with Uvicorn workers
```bash
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

---

## Error Handling

### Custom Exceptions
**Location:** `app/core/exceptions.py`

```python
class ArticleNotFoundError(Exception)
class ChatGenerationError(Exception)
class QuotaExceededError(Exception)
```

### Global Exception Handler
**Location:** `app/main.py`

```python
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )
```

---

## Logging

### Setup
**Location:** `app/core/logging.py`

**Library:** loguru

**Format:**
```
{time} | {level} | {name}:{function}:{line} - {message}
```

**Usage:**
```python
from app.core.logging import get_logger

logger = get_logger(__name__)
logger.info("Processing article")
logger.error("Failed to fetch", exc_info=True)
```

---

## Testing Strategy

### Manual Testing
1. Start services: `docker-compose up`
2. Test endpoints with curl/Postman
3. Check logs: `docker-compose logs -f api`

### API Testing
```bash
# Get articles
curl http://localhost:8000/api/v1/articles/recent?limit=5

# Chat
curl -X POST http://localhost:8000/api/v1/chat/1 \
  -H "Content-Type: application/json" \
  -d '{"message": "What is this article about?"}'

# Check quota
curl http://localhost:8000/api/v1/usage/stats/1
```

### Database Inspection
```bash
docker-compose exec db psql -U postgres -d blips
\dt  # List tables
SELECT * FROM articles LIMIT 5;
```

---

## Common Development Tasks

### Adding a New RSS Feed
1. Edit `app/core/config.py`
2. Add URL to `RSS_FEEDS` list
3. Restart scheduler or trigger `/articles/fetch`

### Adding a New Endpoint
1. Create route in `app/api/routes/`
2. Add schema in `app/schemas/`
3. Implement service logic in `app/services/`
4. Register router in `app/api/__init__.py`

### Adding a New Model
1. Create model in `app/models/`
2. Create repository in `app/repositories/`
3. Generate migration: `alembic revision --autogenerate`
4. Apply: `alembic upgrade head`

---

## Performance Optimization

### Database
- Indexes on frequently queried columns (source_url, published_date, hot_score)
- Connection pooling via SQLAlchemy
- Eager loading for relationships (use `joinedload`)

### Caching
- Redis for hot data (recent articles)
- 5-minute TTL for cached articles
- Cache invalidation on new articles

### Background Jobs
- Separate transactions for batch operations
- Error handling per item (don't fail entire batch)
- Logging for monitoring

---

## Troubleshooting

### Common Issues

**1. Database connection error**
```bash
# Check if DB is running
docker-compose ps db

# Restart
docker-compose restart db

# Check logs
docker-compose logs db
```

**2. Redis connection error**
```bash
# Check if Redis is running
docker-compose ps redis

# Test connection
docker-compose exec redis redis-cli ping
```

**3. OpenAI API errors**
- Verify `OPENAI_API_KEY` in `.env`
- Check OpenAI dashboard for quota
- Frontend can override with `X-OpenAI-Key` header

**4. Articles not fetching**
- Check scheduler logs: `docker-compose logs api | grep scheduler`
- Manually trigger: `POST /api/v1/articles/fetch`
- Verify RSS feed URLs are accessible

**5. Migrations fail**
```bash
# Reset database (DEV ONLY)
docker-compose down -v
docker-compose up -d
alembic upgrade head
```

---

## API Response Examples

### GET /articles/recent
```json
{
  "articles": [
    {
      "id": 1,
      "title": "New AI Model Breakthrough",
      "summary": "Researchers announce...",
      "source_url": "https://techcrunch.com/...",
      "image_url": "https://...",
      "published_date": "2024-12-19",
      "created_at": "2024-12-19T10:30:00",
      "read_time_minutes": 5,
      "tags": [
        {"name": "AI"},
        {"name": "Research"}
      ]
    }
  ]
}
```

### POST /chat/{article_id}
```json
Request:
{
  "message": "What are the key takeaways?"
}

Response:
{
  "response": "The key takeaways are...",
  "remaining_daily": 4,
  "remaining_article": 2
}
```

### GET /usage/stats/{article_id}
```json
{
  "remaining_daily_messages": 5,
  "remaining_article_messages": 3
}
```

---

## Development Workflow

### Local Development
```bash
# 1. Start services
docker-compose up -d

# 2. Apply migrations
docker-compose exec api alembic upgrade head

# 3. View logs
docker-compose logs -f api

# 4. Test API
curl http://localhost:8000/api/v1/articles/recent
```

### Making Changes
```bash
# 1. Edit code (hot-reload enabled)
vim app/api/routes/articles.py

# 2. Test change
curl http://localhost:8000/api/v1/...

# 3. Check logs
docker-compose logs api
```

### Adding Dependencies
```bash
# 1. Add to requirements.txt
echo "requests==2.31.0" >> requirements.txt

# 2. Rebuild container
docker-compose build api

# 3. Restart
docker-compose up -d api
```

---

## Security Considerations

### API Keys
- OpenAI key stored in `.env` (not in git)
- Frontend can provide custom key via `X-OpenAI-Key` header
- Validate all external inputs with Pydantic

### CORS
- Configured in `main.py`
- Allows all origins in development
- Restrict in production

### SQL Injection
- SQLAlchemy ORM prevents SQL injection
- Always use parameterized queries

### Rate Limiting
- Quota system for chat (5/day, 3/article)
- Consider adding IP-based rate limiting for production

---

## Monitoring & Logging

### Logs
**Location:** Docker logs or stdout

```bash
# View all logs
docker-compose logs -f

# API logs only
docker-compose logs -f api

# Search logs
docker-compose logs api | grep ERROR
```

### Metrics to Monitor
- Article fetch success rate
- API response times
- Database connection pool usage
- Redis hit rate
- OpenAI API usage

---

## Next Steps for Agents

When working on this project:

1. **Understand the Architecture**
   - Review layered architecture (API → Service → Repository → Model)
   - Follow repository pattern for database access
   - Use dependency injection for services

2. **Use Existing Patterns**
   - Look at existing routes for examples
   - Follow naming conventions (e.g., `get_*`, `create_*`, `update_*`)
   - Use Pydantic for validation

3. **Test Thoroughly**
   - Test API endpoints with curl
   - Check database state with psql
   - Monitor logs for errors

4. **Database Changes**
   - Always use Alembic for migrations
   - Review generated migrations before applying
   - Add indexes for performance

5. **Error Handling**
   - Use custom exceptions
   - Log errors with context
   - Return meaningful error messages

---

## Related Documentation

- **Frontend Guide:** See `tech-whisperer-digest/AGENT_GUIDE.md`
- **Copilot Instructions:** `.github/copilot-instructions.md`
- **FastAPI Docs:** https://fastapi.tiangolo.com
- **SQLAlchemy Docs:** https://docs.sqlalchemy.org
- **Alembic Docs:** https://alembic.sqlalchemy.org

---

## Quick Reference

### File to Edit for...
- API endpoints: `app/api/routes/`
- Business logic: `app/services/`
- Database models: `app/models/`
- Configuration: `app/core/config.py`
- Background jobs: `app/scheduler/tasks.py`

### Commands
```bash
docker-compose up -d             # Start services
docker-compose logs -f api       # View logs
alembic upgrade head             # Apply migrations
docker-compose exec db psql -U postgres -d blips  # DB shell
docker-compose exec redis redis-cli              # Redis shell
```

### Port Numbers
- API: `8000`
- PostgreSQL: `5432`
- Redis: `6379`

---

**Last Updated:** December 2024
**Maintainer:** Refer to git history
**Questions?** Check frontend guide and copilot instructions
