# Copilot Instructions

## Project Context
This is the backend for a mobile news feed app. It fetches articles from RSS feeds and videos from YouTube channels, processes them, and serves them via API.

## Development Guidelines

### 1. Research First
Before writing code, search for how this problem is commonly solved in production apps. Look for established patterns, popular libraries, and best practices for Python/FastAPI backends.

### 2. Use Existing Solutions
Don't reinvent the wheel. If there's a well-maintained library that solves this problem, use it properly rather than hacking together a custom solution. Examples:
- `feedparser` for RSS parsing
- `SQLAlchemy` patterns for database operations
- `Pydantic` for validation
- `APScheduler` for background jobs

### 3. Follow Framework Conventions
Use FastAPI idiomatically:
- Dependency injection for database sessions
- Pydantic models for request/response schemas
- Proper async/await patterns
- Repository pattern for database access

### 4. Ask Before Hacking
If you're about to write custom code to work around a limitation, stop and ask if there's a standard way to do this first.

### 5. Read Documentation
When using a library, read its docs to understand the intended usage pattern, not just make it "work."

### 6. Database Best Practices
- Use Alembic for all migrations
- Follow the repository pattern (db/repos/)
- Use proper indexes for frequently queried columns
- Consider Redis caching for hot data

### 7. Expand and Understand the Full Context
When given a short or partial prompt, expand it to understand the complete functionality being discussed. Before implementing:
- Think about all related aspects of the feature (e.g., if fixing article ordering, consider videos too)
- Consider edge cases and all API paths affected
- Verify your changes cover the entire scope, not just the specific case mentioned
- After implementing, mentally walk through all scenarios to ensure nothing was missed

## Tech Stack
- **API**: FastAPI
- **Database**: PostgreSQL + SQLAlchemy + Alembic
- **Cache**: Redis
- **Scheduler**: APScheduler
- **Containerization**: Docker + Docker Compose

## Project Structure
```
src/backend/
├── app/
│   ├── api/          # Route handlers
│   ├── db/           # Database models and repos
│   ├── models/       # SQLAlchemy models
│   ├── schemas/      # Pydantic schemas
│   ├── services/     # Business logic
│   └── scheduler/    # Background jobs
├── alembic/          # Database migrations
└── requirements.txt
```

## Frontend
The current mobile client is in a separate repo (`blips-mobile`) and is built with Flutter for iOS and Android.
