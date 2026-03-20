# Development Guide

This guide explains how to set up, run, and test the Blips project locally.

---

## Prerequisites

### Backend

- **Python 3.11+**: `python3 --version`
- **Docker & Docker Compose**: `docker --version`
- **PostgreSQL client** (optional): `psql --version`

### Mobile

- **Flutter 3.16+**: `flutter --version`
- **Xcode 15+** (for iOS): Via Mac App Store
- **CocoaPods**: `pod --version`
- **iOS Simulator** or physical device

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/blips.git
cd blips
```

### 2. Backend Setup

```bash
cd blips-ai-news-backend

# Create environment file
cp src/backend/.env.example src/backend/.env
# Edit .env and add your OPENAI_API_KEY

# Start services
docker compose -f src/docker-compose.yml up -d

# Run migrations
docker compose -f src/docker-compose.yml exec api alembic upgrade head

# Optional: populate initial content immediately instead of waiting for the scheduler
docker compose -f src/docker-compose.yml exec api python scripts/trigger_ingestion.py
```

Verify it's working:
```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy"}
```

### 3. Mobile Setup

```bash
cd blips-mobile

# Get dependencies
flutter pub get

# iOS only: Install pods
cd ios && pod install && cd ..

# Run on simulator
flutter run
```

---

## Running the Backend

### With Docker (Recommended)

```bash
cd blips-ai-news-backend

# Start all services
docker compose -f src/docker-compose.yml up -d

# View logs
docker compose -f src/docker-compose.yml logs -f api

# Stop services
docker compose -f src/docker-compose.yml down
```

### Without Docker (Development)

```bash
cd blips-ai-news-backend/src/backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL and Redis separately
# (Assumes they're running locally)

# Set environment
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/blips
export REDIS_URL=redis://localhost:6379/0
export OPENAI_API_KEY=sk-your-key

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload --port 8000
```

---

## Running the Mobile App

### iOS Simulator

```bash
cd blips-mobile

# List available simulators
flutter devices

# Run on specific simulator
flutter run -d "iPhone 16"

# Or use device ID
flutter run -d 0733A81B-8891-4FF9-9CA2-39DF4A49748B
```

### iOS Physical Device

1. Open `ios/Runner.xcworkspace` in Xcode
2. Select your team in Signing & Capabilities
3. Connect device via USB
4. `flutter run -d <device-id>`

### Android Emulator

```bash
# Start emulator
flutter emulators --launch <emulator-name>

# Run app
flutter run
```

### Hot Reload vs Hot Restart

- **Hot Reload** (`r`): Updates UI without losing state. Use for UI changes.
- **Hot Restart** (`R`): Restarts app, loses state. Use for state/provider changes.

---

## Database Operations

### View Database

```bash
# Connect via Docker
docker compose -f src/docker-compose.yml exec db psql -U postgres -d blips

# Common queries
SELECT COUNT(*) FROM content_items;
SELECT * FROM content_items ORDER BY created_at DESC LIMIT 5;
SELECT * FROM feed_sources WHERE is_active = true;
```

### Run Migrations

```bash
# Generate new migration
docker compose -f src/docker-compose.yml exec api alembic revision --autogenerate -m "description"

# Apply migrations
docker compose -f src/docker-compose.yml exec api alembic upgrade head

# Rollback one migration
docker compose -f src/docker-compose.yml exec api alembic downgrade -1

# View migration history
docker compose -f src/docker-compose.yml exec api alembic history
```

### Reset Database

```bash
# Drop and recreate
docker compose -f src/docker-compose.yml down -v  # -v removes volumes
docker compose -f src/docker-compose.yml up -d
docker compose -f src/docker-compose.yml exec api alembic upgrade head
```

---

## Testing

### Backend Tests

```bash
cd blips-ai-news-backend/src/backend

# Run all tests
pytest

# Run specific test file
pytest tests/test_ranking.py

# Run with coverage
pytest --cov=app --cov-report=html

# Run only unit tests (fast)
pytest -m "not integration"
```

### Mobile Tests

```bash
cd blips-mobile

# Run unit tests
flutter test

# Run specific test file
flutter test test/features/feed/feed_repository_test.dart

# Run with coverage
flutter test --coverage

# Run integration tests (requires running emulator)
flutter test integration_test
```

### Test the API Manually

```bash
# Health check
curl http://localhost:8000/health

# Get articles
curl http://localhost:8000/api/v1/articles/recent

# Get videos
curl http://localhost:8000/api/v1/videos/recent

# Get reels
curl http://localhost:8000/api/v1/videos/reels

# Post interaction
curl -X POST http://localhost:8000/api/v1/session/interactions \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: test-device-1234" \
  -d '{"content_item_id": 1, "event_type": "VIEW_10S"}'
```

---

## Debugging

### Backend Debugging

**View logs**:
```bash
docker compose -f src/docker-compose.yml logs -f api
```

**Python shell with app context**:
```bash
docker compose -f src/docker-compose.yml exec api python
```
```python
from app.db.base import SessionLocal
from app.models.content import ContentType
from app.repositories.content_repo import ContentItemRepository

db = SessionLocal()
repo = ContentItemRepository(db)
items = repo.get_by_type(
    ContentType.ARTICLE,
    limit=5,
    hours_back=72,
    ai_processed_only=True,
)
print(items)
```

**Debug specific job**:
```python
from app.scheduler.tasks import fetch_and_process_news

fetch_and_process_news()  # Runs immediately
```

### Mobile Debugging

**Flutter DevTools**:
```bash
flutter run
# Note the DevTools URL in console
# Open in browser for debugging
```

**Print debugging**:
```dart
debugPrint('Value: $someValue');  // Better than print()
```

**Inspect Riverpod state**:
```dart
// In widget
final state = ref.watch(someProvider);
debugPrint('State: $state');
```

### Common Debug Commands

```bash
# Check if services are running
docker compose -f src/docker-compose.yml ps

# Check container logs
docker compose -f src/docker-compose.yml logs api
docker compose -f src/docker-compose.yml logs db
docker compose -f src/docker-compose.yml logs redis

# Check database connectivity
docker compose -f src/docker-compose.yml exec api python -c "from sqlalchemy import text; from app.db.base import engine; conn = engine.connect(); print(conn.execute(text('SELECT 1')).scalar()); conn.close()"

# Check Redis connectivity
docker compose -f src/docker-compose.yml exec redis redis-cli PING
```

---

## Common Pitfalls

### 1. "Connection refused" to database

**Cause**: Wrong hostname in DATABASE_URL
**Fix**:
- Docker: Use `db` (service name)
- Local: Use `localhost`

### 2. Migrations fail with "relation already exists"

**Cause**: Database out of sync with migrations
**Fix**:
```bash
# Check current revision
docker compose -f src/docker-compose.yml exec api alembic current

# If stuck, reset
docker compose -f src/docker-compose.yml exec api alembic stamp head
```

### 3. Mobile app shows "No items"

**Cause**: Backend not running or no data
**Fix**:
1. Check backend: `curl http://localhost:8000/health`
2. Check data: `curl http://localhost:8000/api/v1/articles/recent`
3. Run fetch job: `docker compose -f src/docker-compose.yml exec api python -c "from app.scheduler.tasks import fetch_and_process_news; fetch_and_process_news()"`

### 4. Videos don't play on iOS simulator

**Cause**: YouTube playback requests are being throttled or the simulator network is flaky
**Fix**: Wait a few minutes, verify `/api/v1/videos/recent` still returns items, and retry on a physical device if the simulator remains unstable.

### 5. Hot reload doesn't update providers

**Cause**: Providers are cached
**Fix**: Use hot restart (`R`) instead of hot reload (`r`)

### 6. "No pubspec.yaml found"

**Cause**: Running flutter from wrong directory
**Fix**: `cd` to `blips-mobile` directory first

### 7. iOS build fails with signing error

**Cause**: No development team selected
**Fix**:
1. Open Xcode
2. Select Runner target
3. Go to Signing & Capabilities
4. Select your team

### 8. Docker containers keep restarting

**Cause**: Application crashing on startup
**Fix**:
```bash
# Check logs
docker compose -f src/docker-compose.yml logs api

# Common issues:
# - Missing OPENAI_API_KEY
# - Database not ready (wait a few seconds)
# - Port already in use
```

---

## Development Workflow

### Making Backend Changes

1. Make code changes
2. Docker auto-reloads (if using --reload)
3. Test: `curl http://localhost:8000/api/v1/your-endpoint`
4. Run tests: `pytest tests/test_your_feature.py`
5. Commit

### Making Mobile Changes

1. Make code changes
2. Hot reload (`r`) for UI changes
3. Hot restart (`R`) for state/provider changes
4. Test on simulator
5. Run tests: `flutter test`
6. Commit

### Adding a New Feature

1. **Backend**:
   - Add model (if needed)
   - Create migration
   - Add repository method
   - Add route
   - Add tests

2. **Mobile**:
   - Add DTO (if new API response)
   - Add domain model
   - Add repository method
   - Add provider
   - Add UI widget
   - Add tests

### Before Committing

```bash
# Backend
cd blips-ai-news-backend/src/backend
pytest
ruff check app/  # Linting

# Mobile
cd blips-mobile
flutter test
flutter analyze  # Linting
```

---

## Useful Commands Cheatsheet

```bash
# Backend
docker compose -f src/docker-compose.yml up -d              # Start services
docker compose -f src/docker-compose.yml down               # Stop services
docker compose -f src/docker-compose.yml logs -f api        # View API logs
docker compose -f src/docker-compose.yml exec api alembic upgrade head  # Run migrations
docker compose -f src/docker-compose.yml exec db psql -U postgres -d blips  # Database shell

# Mobile
flutter run                       # Run app
flutter run -d <device>           # Run on specific device
flutter test                      # Run tests
flutter pub get                   # Get dependencies
flutter clean                     # Clean build
flutter analyze                   # Run linter

# Both
git status                        # Check changes
git diff                          # View changes
git add .                         # Stage all
git commit -m "message"           # Commit
```
