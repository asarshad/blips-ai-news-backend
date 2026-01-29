# Development Guide

This guide covers setting up and developing the Blips backend.

## Prerequisites

- Python 3.11 or 3.12 (Python 3.13+ currently unsupported due to `feedparser` depending on the removed stdlib `cgi` module)
- PostgreSQL 14+
- Redis 7+
- Docker & Docker Compose (recommended)

## Quick Start

### Using Docker (Recommended)

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop
docker-compose down
```

### Local Development

1. **Create virtual environment**:

```bash
cd src/backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. **Install dependencies**:

```bash
pip install -r requirements.txt
```

3. **Set up environment**:

```bash
cp .env.example .env
# Edit .env with your settings
```

4. **Run migrations**:

```bash
alembic upgrade head
```

5. **Start the server**:

```bash
uvicorn app.main:app --reload --port 8000
```

## Project Structure

```
src/backend/
├── alembic/              # Database migrations
├── app/
│   ├── api/              # Route handlers
│   ├── config/           # Configuration modules
│   ├── core/             # Shared utilities
│   ├── db/               # Database setup
│   ├── models/           # SQLAlchemy models
│   ├── repositories/     # Data access layer
│   ├── scheduler/        # Background jobs
│   ├── services/         # Business logic
│   ├── clustering/       # Clustering domain
│   ├── ingestion/        # Ingestion domain
│   └── ranking/          # Ranking domain
├── tests/                # Test files
└── requirements.txt
```

## Coding Standards

### File Size Limit

**No file should exceed 300 lines.** If a file grows beyond this:

1. Extract related functions into a separate module
2. Split classes into smaller, focused classes
3. Move constants to `app/config/`

### No Magic Numbers

All constants must be defined in `app/config/`:

```python
# ❌ Bad
def compute_score(age_hours):
    return 2 ** (-age_hours / 24)  # Magic number!

# ✅ Good
from app.config.scoring import recency_config

def compute_score(age_hours):
    return 2 ** (-age_hours / recency_config.half_life_hours)
```

### Domain-Driven Structure

Organize code by domain, not by type:

```python
# ❌ Bad: services/scoring_service.py with 400 lines

# ✅ Good: ranking/ domain module
ranking/
├── __init__.py
├── quality.py      # Quality score
├── recency.py      # Recency score
├── trend.py        # Trend score
├── diversity.py    # Diversity boost
├── global_score.py # Combined score
└── service.py      # Orchestration
```

### Thin Controllers

Route handlers should be thin - no business logic:

```python
# ❌ Bad
@router.get("/feed")
def get_feed(db: Session = Depends(get_db)):
    items = db.query(ContentItem).filter(...).all()
    for item in items:
        # Lots of business logic here
        item.score = compute_score(item)
    return items

# ✅ Good
@router.get("/feed")
def get_feed(
    service: FeedService = Depends(get_feed_service)
):
    return service.get_personalized_feed()
```

### Repository Pattern

Data access should go through repositories:

```python
# ❌ Bad: Direct DB access in service
class MyService:
    def get_data(self):
        return self.db.query(Model).filter(...).all()

# ✅ Good: Use repository
class MyService:
    def __init__(self, repo: MyRepository):
        self.repo = repo
    
    def get_data(self):
        return self.repo.get_recent(hours_back=24)
```

## Testing

### Run Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=html

# Specific file
pytest tests/test_scoring.py

# Specific test
pytest tests/test_scoring.py::test_recency_decay
```

### Test Structure

```python
# tests/test_scoring.py
def test_recency_decay():
    """Score should halve every 24 hours."""
    from app.ranking.recency import compute_recency_score
    from datetime import datetime, timedelta, timezone
    
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(hours=24)
    
    score_now = compute_recency_score(now)
    score_day_ago = compute_recency_score(one_day_ago)
    
    assert score_now == 1.0
    assert abs(score_day_ago - 0.5) < 0.01
```

## Database Migrations

### Create Migration

```bash
alembic revision --autogenerate -m "Add new column"
```

### Apply Migrations

```bash
# Upgrade to latest
alembic upgrade head

# Downgrade one step
alembic downgrade -1

# Show current revision
alembic current
```

### Migration Best Practices

1. Review auto-generated migrations before applying
2. Test migrations on a copy of production data
3. Include both upgrade and downgrade paths

## Adding New Features

### 1. Add Configuration

If the feature has tunable parameters, add to `app/config/`:

```python
# app/config/my_feature.py
from pydantic_settings import BaseSettings

class MyFeatureConfig(BaseSettings):
    some_threshold: float = 0.5
    
    class Config:
        env_prefix = "MY_FEATURE_"

my_feature_config = MyFeatureConfig()
```

### 2. Create Domain Module

For significant features, create a domain module:

```python
# app/my_feature/__init__.py
from app.my_feature.core import compute_something
from app.my_feature.service import MyFeatureService

__all__ = ["compute_something", "MyFeatureService"]
```

### 3. Add Repository Methods

If you need new database queries:

```python
# app/repositories/my_repo.py
class MyRepository(BaseRepository):
    def get_by_criteria(self, criteria: str) -> List[Model]:
        return self.db.query(Model).filter(
            Model.criteria == criteria
        ).all()
```

### 4. Create Service

Orchestrate the feature in a service:

```python
# app/my_feature/service.py
class MyFeatureService:
    def __init__(self, repo: MyRepository):
        self.repo = repo
    
    def do_something(self) -> Result:
        data = self.repo.get_by_criteria("...")
        return process(data)
```

### 5. Add API Endpoint

Expose via thin controller:

```python
# app/api/my_feature.py
@router.get("/my-feature")
def get_my_feature(
    service: MyFeatureService = Depends(get_service)
):
    return service.do_something()
```

## Debugging

### Logging

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

logger.info("Processing item", extra={"item_id": 123})
logger.error("Failed to process", exc_info=True)
```

### Interactive Debugging

```bash
# Start with debugger
python -m pdb -m uvicorn app.main:app --reload
```

Or use VS Code's Python debugger with launch.json.

## Deployment

See [deployment-guide.md](../../deployment-guide.md) for production deployment.
