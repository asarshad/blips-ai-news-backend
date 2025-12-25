# Backend Structure Guide

## Overview

The backend is a Python FastAPI application that handles content ingestion, processing, and serving. It runs in Docker alongside PostgreSQL and Redis.

## Directory Structure

```
src/backend/
├── alembic/                 # Database migrations
│   └── versions/           # Migration files (auto-generated)
├── app/
│   ├── api/                # HTTP endpoints
│   │   └── routes/         # Route handlers by domain
│   ├── clustering/         # Content deduplication
│   ├── config/             # Settings and configuration
│   ├── core/               # Shared utilities
│   ├── db/                 # Database setup
│   ├── ingestion/          # Content fetching
│   ├── integrations/       # External services
│   ├── models/             # SQLAlchemy models
│   ├── ranking/            # Score calculation
│   ├── repositories/       # Data access layer
│   ├── scheduler/          # Background jobs
│   ├── schemas/            # Pydantic models
│   └── services/           # Business logic
├── requirements.txt
└── Dockerfile
```

## Folder-by-Folder Explanation

### `/app/api/routes/`

HTTP endpoint handlers. Each file handles one resource type.

| File | Purpose |
|------|---------|
| `articles.py` | Article listing, detail, search |
| `videos.py` | Video listing, reels endpoint |
| `session.py` | Playlist generation, engagement |
| `interactions.py` | Log user events (view, like, share) |
| `chat.py` | AI chat about articles |
| `sources.py` | Feed source management |
| `health.py` | Health check endpoint |

**Pattern**: Routes are thin. They validate input, call a service, return response.

```python
# Good: Thin route
@router.get("/recent")
async def get_recent(limit: int = 20, db: Session = Depends(get_db)):
    repo = ContentItemRepository(db)
    return repo.get_recent_articles(limit=limit)

# Bad: Business logic in route
@router.get("/recent")  
async def get_recent(limit: int = 20, db: Session = Depends(get_db)):
    items = db.query(ContentItem).filter(...).all()  # Don't do this
    for item in items:
        item.score = calculate_score(item)  # Logic belongs in service
    return items
```

### `/app/models/`

SQLAlchemy ORM models. These define the database schema.

| File | Tables |
|------|--------|
| `content.py` | `content_items` - Articles and videos |
| `source.py` | `feed_sources` - RSS feeds and channels |
| `interaction.py` | `interaction_events` - User engagement |
| `user.py` | `user_preferences` - Category weights |

**Important**: Models are declarative. No business logic here.

### `/app/repositories/`

Data access layer. All database queries live here.

**Why repositories?**
- Testable: Can mock for unit tests
- Reusable: Same query used by multiple services
- Consistent: All queries follow same patterns

```python
# Example: content_repo.py
class ContentItemRepository:
    def __init__(self, db: Session):
        self.db = db
    
    def get_recent_articles(self, limit: int = 20):
        """Get recent articles, sorted by score."""
        return self.db.query(ContentItem)\
            .filter(ContentItem.content_type == ContentType.ARTICLE)\
            .order_by(ContentItem.global_score.desc())\
            .limit(limit)\
            .all()
```

### `/app/services/`

Business logic layer. Orchestrates operations across repositories.

| File | Responsibility |
|------|----------------|
| `playlist_service.py` | Generate personalized feeds |
| `chat_service.py` | AI chat with context management |

### `/app/ingestion/`

Content fetching from external sources.

| File | Purpose |
|------|---------|
| `service.py` | Main ingestion orchestrator |
| `extractors.py` | Source-specific parsers |

**How to add a new content source** (see dedicated section below).

### `/app/ranking/`

Score calculation modules. Each file computes one score component.

| File | Score Type | Range |
|------|------------|-------|
| `quality.py` | Content quality | 0-1 |
| `recency.py` | Time freshness | 0-1 |
| `trend.py` | Engagement velocity | 0-1 |
| `diversity.py` | Topic balance boost | 0-0.2 |
| `global_score.py` | Combined score | 0-1 |
| `service.py` | Orchestrates scoring job | - |

### `/app/clustering/`

Groups related content to avoid duplicates.

| File | Purpose |
|------|---------|
| `service.py` | Main clustering logic |
| `similarity.py` | Similarity calculations |

### `/app/config/`

All configuration centralized here.

| File | Purpose |
|------|---------|
| `settings.py` | Environment variables, defaults |
| `clustering.py` | Clustering thresholds |
| `ranking.py` | Score weights |
| `sources.py` | Default RSS feeds and channels |

### `/app/scheduler/`

Background job definitions.

| File | Jobs |
|------|------|
| `jobs.py` | Job functions (fetch_news, run_scoring, etc.) |
| `scheduler.py` | APScheduler setup and registration |

### `/app/core/`

Shared utilities.

| File | Purpose |
|------|---------|
| `logging.py` | Structured logging setup |
| `deps.py` | FastAPI dependency injection |

---

## How to Add a New Content Source

### Adding an RSS Feed

1. **Add to database** via API or directly:

```sql
INSERT INTO feed_sources (name, url, source_type, category, is_active)
VALUES ('New Tech Blog', 'https://example.com/rss', 'rss', 'Technology', true);
```

2. **Or update default sources** in `/app/config/sources.py`:

```python
DEFAULT_RSS_FEEDS = [
    # ... existing feeds
    {
        "name": "New Tech Blog",
        "url": "https://example.com/rss",
        "category": "Technology",
    },
]
```

3. **Restart the scheduler** to pick up new source.

### Adding a YouTube Channel

1. **Add to database**:

```sql
INSERT INTO feed_sources (name, url, source_type, category, is_active)
VALUES ('New Channel', 'UC_channel_id', 'youtube', 'Technology', true);
```

2. **Ensure channel allows embedding** (some don't).

### Adding a New Source Type (e.g., Reddit)

1. **Create extractor** in `/app/ingestion/extractors.py`:

```python
class RedditExtractor:
    def __init__(self, subreddit: str):
        self.subreddit = subreddit
        self.client = praw.Reddit(...)  # Add to requirements.txt
    
    def fetch_posts(self, limit: int = 20) -> List[Dict]:
        """Fetch top posts from subreddit."""
        posts = []
        for submission in self.client.subreddit(self.subreddit).hot(limit=limit):
            posts.append({
                "title": submission.title,
                "url": submission.url,
                "source": f"r/{self.subreddit}",
                "published": datetime.fromtimestamp(submission.created_utc),
                "content_type": "article",  # or "video" for video posts
            })
        return posts
```

2. **Add to ingestion service** in `/app/ingestion/service.py`:

```python
def ingest_reddit(self, subreddit: str):
    extractor = RedditExtractor(subreddit)
    posts = extractor.fetch_posts()
    for post in posts:
        self._save_content_item(post)
```

3. **Add source type to model** in `/app/models/source.py`:

```python
class SourceType(enum.Enum):
    RSS = "rss"
    YOUTUBE = "youtube"
    REDDIT = "reddit"  # Add this
```

4. **Create migration**:

```bash
alembic revision --autogenerate -m "add reddit source type"
alembic upgrade head
```

5. **Schedule ingestion** in `/app/scheduler/jobs.py`:

```python
def fetch_reddit():
    """Scheduled job to fetch Reddit posts."""
    service = IngestionService(db)
    service.ingest_reddit("technology")
```

---

## How Ranking Works

### Score Components

Each content item has four score components:

#### 1. Quality Score (0-1)

Measures intrinsic content quality.

```python
def compute_quality_score(item):
    # Factors:
    # - Entity count (more named entities = more substantial)
    # - Content length (too short = low effort)
    # - Source authority (configurable per source)
    
    entity_score = min(len(item.entities) / 5, 1.0)  # Max at 5 entities
    length_score = min(len(item.summary) / 200, 1.0)  # Max at 200 chars
    source_boost = SOURCE_AUTHORITY.get(item.source, 0.5)
    
    return (entity_score * 0.4 + length_score * 0.3 + source_boost * 0.3)
```

#### 2. Recency Score (0-1)

Time decay function. Fresh content ranks higher.

```python
def compute_recency_score(item):
    age_hours = (now - item.published_at).total_seconds() / 3600
    
    # Exponential decay with half-life of 12 hours
    half_life = 12
    return 0.5 ** (age_hours / half_life)
```

**Visualization**:
```
Score │ 
1.0   │████
0.75  │    ████
0.5   │        ████
0.25  │            ████
0     └────────────────── Hours
      0   12   24   36
```

#### 3. Trend Score (0-1)

Engagement velocity. Items getting more interactions recently rank higher.

```python
def compute_trend_score(item, interactions):
    # Count interactions in last 6 hours vs last 24 hours
    recent = count_interactions(item, hours=6)
    baseline = count_interactions(item, hours=24)
    
    if baseline == 0:
        return 0.5  # Default for new content
    
    velocity = recent / (baseline / 4)  # Normalized to same time period
    return min(velocity, 1.0)
```

#### 4. Diversity Boost (0-0.2)

Bonus for underrepresented categories in the feed.

```python
def compute_diversity_boost(item, topic_distribution):
    # If AI category is 40% of recent content but user prefers 50%
    # Give AI articles a small boost
    current_share = topic_distribution.get(item.category, 0)
    target_share = user_prefs.get(item.category, 0.2)
    
    if current_share < target_share:
        return 0.2 * (target_share - current_share)
    return 0
```

### Global Score Calculation

```python
def compute_global_score(item):
    weights = {
        "quality": 0.25,
        "recency": 0.35,
        "trend": 0.25,
        "diversity": 0.15,
    }
    
    return (
        weights["quality"] * item.quality_score +
        weights["recency"] * item.recency_score +
        weights["trend"] * item.trend_score +
        weights["diversity"] * compute_diversity_boost(item)
    )
```

### When Scores Update

- **On ingestion**: Initial quality and recency scores set
- **Every 60 minutes**: Scoring job recalculates all scores for recent content
- **Never real-time**: Too expensive. Cached scores are good enough.

---

## Database Schema Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       content_items                              │
├─────────────────┬───────────────────────────────────────────────┤
│ id              │ Primary key                                   │
│ title           │ Article/video title                           │
│ summary         │ AI-generated 2-sentence summary               │
│ url             │ Original source URL                           │
│ source          │ Publisher name                                │
│ content_type    │ 'article' | 'video'                           │
│ video_url       │ Direct video URL (for videos)                 │
│ duration_seconds│ Video length                                  │
│ category        │ AI-classified category                        │
│ entities        │ JSON array of named entities                  │
│ cluster_id      │ FK to cluster (for deduplication)             │
│ is_representative│ True if best in cluster                      │
│ quality_score   │ 0-1                                           │
│ recency_score   │ 0-1                                           │
│ trend_score     │ 0-1                                           │
│ global_score    │ 0-1 (combined)                                │
│ created_at      │ When we fetched it                            │
│ published_at    │ When source published it                      │
└─────────────────┴───────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       feed_sources                               │
├─────────────────┬───────────────────────────────────────────────┤
│ id              │ Primary key                                   │
│ name            │ Display name                                  │
│ url             │ RSS URL or YouTube channel ID                 │
│ source_type     │ 'rss' | 'youtube'                             │
│ category        │ Default category for items                    │
│ is_active       │ Whether to fetch from this source             │
│ last_fetched    │ Timestamp of last successful fetch            │
└─────────────────┴───────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    interaction_events                            │
├─────────────────┬───────────────────────────────────────────────┤
│ id              │ Primary key                                   │
│ user_id         │ Anonymous user identifier                     │
│ content_id      │ FK to content_items                           │
│ event_type      │ 'view' | 'like' | 'share' | 'save'            │
│ created_at      │ When interaction happened                     │
└─────────────────┴───────────────────────────────────────────────┘
```

---

## Common Patterns

### Adding a New API Endpoint

1. Create route in `/app/api/routes/`:

```python
@router.get("/new-endpoint")
async def new_endpoint(db: Session = Depends(get_db)):
    repo = SomeRepository(db)
    return repo.get_something()
```

2. Register in `/app/api/routes/__init__.py`:

```python
from app.api.routes import new_module
api_router.include_router(new_module.router, prefix="/new", tags=["new"])
```

### Adding a New Database Column

1. Update model in `/app/models/`
2. Generate migration: `alembic revision --autogenerate -m "description"`
3. Review migration file in `/alembic/versions/`
4. Apply: `alembic upgrade head`

### Debugging a Scoring Issue

1. Check item in database: `SELECT * FROM content_items WHERE id = X`
2. Run scoring manually in Python shell:
   ```python
   from app.ranking.service import ScoringService
   service = ScoringService(content_repo, interaction_repo)
   scores = service.score_single_item(item)
   print(scores)
   ```
3. Check individual score components in `/app/ranking/*.py`
