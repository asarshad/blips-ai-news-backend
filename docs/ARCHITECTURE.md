# Blips Architecture Overview

## What Is Blips?

Blips is a personalized tech news aggregator with two main components:
- **Backend**: Python/FastAPI service that fetches, processes, and serves content
- **Mobile**: Flutter app that displays articles and short-form video reels

## High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CONTENT SOURCES                             │
├──────────────────┬──────────────────┬───────────────────────────────┤
│   RSS Feeds      │  YouTube Shorts  │      (Future: X, Reddit)      │
│   (Articles)     │  (Video Reels)   │                               │
└────────┬─────────┴────────┬─────────┴───────────────────────────────┘
         │                  │
         ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      INGESTION LAYER                                │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Scheduler (APScheduler)                                     │   │
│  │  - Runs every 30 min for news                               │   │
│  │  - Runs every 15 min for clustering                         │   │
│  │  - Runs every 60 min for scoring                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Content Extractors                                          │   │
│  │  - RSS Parser (feedparser)                                   │   │
│  │  - YouTube Data API                                          │   │
│  │  - Entity extraction (via OpenAI)                            │   │
│  │  - Summarization (via OpenAI)                                │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       STORAGE LAYER                                 │
│                                                                     │
│   ┌─────────────────┐          ┌─────────────────────────────────┐ │
│   │   PostgreSQL    │          │            Redis                │ │
│   │                 │          │                                 │ │
│   │ - content_items │          │ - Session snapshots             │ │
│   │ - feed_sources  │          │ - Playlist cache                │ │
│   │ - interactions  │          │ - Rate limiting                 │ │
│   │ - user_prefs    │          │ - Hot article pre-cache         │ │
│   │ - clusters      │          │                                 │ │
│   └─────────────────┘          └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PROCESSING LAYER                                │
│                                                                     │
│   ┌───────────────────┐    ┌───────────────────────────────────┐   │
│   │    Clustering     │    │           Ranking                 │   │
│   │                   │    │                                   │   │
│   │ Groups duplicate  │    │ Computes scores:                  │   │
│   │ stories using:    │    │ - Quality (entities, length)      │   │
│   │ - Entity overlap  │    │ - Recency (time decay)            │   │
│   │ - Title similarity│    │ - Trend (interaction velocity)    │   │
│   │ - Topic matching  │    │ - Diversity (topic balance)       │   │
│   └───────────────────┘    └───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        API LAYER (FastAPI)                          │
│                                                                     │
│   /api/v1/articles/recent     - Paginated article list              │
│   /api/v1/videos/recent       - Paginated video list                │
│   /api/v1/videos/reels        - Short-form videos for reels         │
│   /api/v1/session/playlist    - Personalized mixed feed             │
│   /api/v1/session/interactions - Log user engagement                │
│   /api/v1/ai/respond          - AI chat about content               │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      MOBILE APP (Flutter)                           │
│                                                                     │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐  │
│   │   Home Feed   │  │  Reels View   │  │     Settings          │  │
│   │               │  │               │  │                       │  │
│   │ Articles +    │  │ TikTok-style  │  │ Category preferences  │  │
│   │ Videos mixed  │  │ vertical      │  │ Source management     │  │
│   │ Riverpod      │  │ scroll        │  │ Theme selection       │  │
│   │ state mgmt    │  │ Video pooling │  │                       │  │
│   └───────────────┘  └───────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Why Clustering?

**Problem**: Multiple sources cover the same story (e.g., "Apple announces iPhone 16" from 10 different sites).

**Solution**: We cluster related content together and show only the highest-quality representative from each cluster.

**Trade-off**: We sacrifice some coverage diversity for a cleaner, less repetitive feed.

### 2. Why Video Player Pooling?

**Problem**: Creating a new video player for each reel causes memory issues and slow loading.

**Solution**: We maintain a pool of 5 pre-initialized players that get recycled as users scroll.

**Trade-off**: More complex state management, but much better performance (< 500ms time-to-first-frame vs 3+ seconds).

### 3. Why Redis + PostgreSQL?

**Problem**: Need both durable storage and fast cache access.

**Solution**:
- PostgreSQL: Source of truth for content, user data, interactions
- Redis: Hot data cache (playlists, sessions, rate limits)

**Trade-off**: Additional infrastructure complexity, but necessary for performance at scale.

### 4. Why Background Jobs Instead of On-Demand?

**Problem**: Fetching RSS feeds and computing scores on each request would be too slow.

**Solution**: APScheduler runs periodic jobs:
- Content ingestion every 30 minutes
- Scoring every 60 minutes  
- Clustering every 15 minutes

**Trade-off**: Content has a slight delay (up to 30 min) but API responses are instant.

### 5. Why Riverpod Over Other State Management?

**Problem**: Need reactive state that survives widget rebuilds and supports async data.

**Solution**: Riverpod with providers for each data domain (feed, settings, chat).

**Trade-off**: Learning curve for new developers, but excellent testability and composition.

## Data Flow: Article from Source to Screen

```
1. RSS Feed publishes new article
                │
                ▼
2. Scheduler triggers fetch job (every 30 min)
                │
                ▼
3. Extractor parses RSS, extracts:
   - Title, URL, content
   - Source metadata
   - Published date
                │
                ▼
4. OpenAI generates:
   - 2-sentence summary
   - Named entities (people, companies, tech)
   - Category classification
                │
                ▼
5. Content saved to PostgreSQL (content_items table)
                │
                ▼
6. Clustering job runs (every 15 min):
   - Compares new content to recent items
   - Assigns cluster_id if similar story exists
   - Updates cluster_representative flag
                │
                ▼
7. Scoring job runs (every 60 min):
   - Computes quality_score (entity count, length)
   - Computes recency_score (time decay)
   - Computes trend_score (interaction velocity)
   - Computes global_score (weighted combination)
                │
                ▼
8. Mobile app requests /api/v1/articles/recent
                │
                ▼
9. Tiered Feed Service blends content:
   - Tier A (Fresh): Recently published
   - Tier B (Backfill): Recently added, older publish
   - Tier C (Evergreen): High quality archive
                │
                ▼
10. Inventory health check:
   - If below threshold, trigger top-up ingestion
   - Cache invalidation on new content
                │
                ▼
11. API returns tiered articles:
   - Filtered to cluster representatives
   - Blended by freshness tier
   - Diversity mixed
   - With tier/age metadata for UI
                │
                ▼
12. Flutter displays in ArticleCard with freshness label
```

## Security Considerations

- **No Auth Yet**: Currently no authentication. Future: Supabase Auth.
- **Rate Limiting**: Redis-based per-IP rate limiting on chat endpoints.
- **API Keys**: OpenAI key stored in environment, never in code.
- **CORS**: Configured for specific origins in production.

## API Documentation

Interactive documentation available at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Scalability Path

Current design supports ~1000 daily users. To scale further:

1. **Read Replicas**: PostgreSQL read replicas for API queries
2. **CDN**: Cache article images and thumbnails
3. **Queue**: Replace direct OpenAI calls with job queue (Celery/RQ)
4. **Sharding**: Partition content_items by date if table grows too large

---

## Code Structure

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
| `inventory_service.py` | Health monitoring with tier counts (A/B/C) |
| `tiered_feed_service.py` | Freshness tier blending for feeds |
| `topup_service.py` | Auto-ingestion when inventory low |
| `playlist_service.py` | Generate personalized feeds |
| `diversity_mixer.py` | Topic/source diversity balancing |
| `personalization_service.py` | User preference learning and decay |
| `quota_manager.py` | AI chat quota management |
| `ai_chat.py` | AI chat with context management |

### `/app/ingestion/`

Content fetching from external sources with checkpoint-based tracking.

| File | Purpose |
|------|---------|
| `service.py` | Main ingestion orchestrator |
| `checkpointing.py` | Checkpoint-based ingestion with budget tracking |
| `checkpoint_worker.py` | Worker for processing individual feeds |
| `extractors.py` | Topic/entity extraction |
| `leases.py` | Distributed locking for feed processing |
| `runtime_state.py` | Scheduler state tracking |

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
| `scoring.py` | Score weights |
| `sources.py` | Default RSS feeds and channels |

### `/app/scheduler/`

Background job definitions.

| File | Jobs |
|------|------|
| `tasks.py` | Main job functions (fetch_and_process_news) |
| `tasks_curation.py` | Scoring, clustering, preference decay jobs |
| `__init__.py` | APScheduler setup and registration |
| `job_stats.py` | Job execution statistics |

### `/app/core/`

Shared utilities.

| File | Purpose |
|------|---------|
| `logging.py` | Structured logging setup |
| `config.py` | Pydantic settings with freshness config |
| `dependencies.py` | FastAPI dependency injection |
| `feature_flags.py` | Feature flag management |

---

## How to Add a New Content Source

### Adding an RSS Feed

1. **Add to database** via API or directly:

```sql
INSERT INTO feed_sources (name, url, source_type, category, is_active)
VALUES ('New Tech Blog', 'https://example.com/rss', 'rss', 'Technology', true);
```

2. **Or update default sources** in `/app/config/feeds.py`:

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

## Personalised Feed Ranking

The base `global_score` is a device-agnostic signal stored in PostgreSQL and
refreshed every 60 minutes.  When a user sends a feed request with an
`X-Device-ID` header, an additional in-memory re-rank pass applies three
per-user adjustments **at serve time**:

### Formula

```
final_score = global_score
            + interest_boost
            - staleness_decay
            - source_dominance_penalty
```

### Components

#### `global_score` (base)

The pre-computed weighted combination of quality, trend, recency, and
diversity (see [Score Components](#score-components) above).  Used as the
starting point for all personalisation.

#### `interest_boost`

Additive bonus for content whose **primary topic** appears in the user's
declared category selection (set during onboarding or settings).

```
interest_boost = INTEREST_WEIGHT × (1 - engagement_decay_factor)
```

- **`INTEREST_WEIGHT = 0.10`** (env: `INTEREST_WEIGHT`).  Calibrated to be
  ≤ 20% of the average base score (~0.50) so declared interests **nudge** but
  never fully override organic quality signals.
- **`engagement_decay_factor`** (0–1): As the user accumulates interaction
  history (learned `UserPreference` weights), the declared-interest boost
  fades.  At `DECAY_SATURATION` total learned weight the boost reaches zero —
  actual behaviour overrides stated preferences.

Non-selected categories are *never filtered out*; every item remains
in the feed, only the ordering changes.

#### `staleness_decay` (penalty)

Supplemental age penalty applied on top of the recency component already
embedded in `global_score`.  Intentionally small to avoid double-counting.

```
staleness_decay = (1 - recency_score) × STALENESS_WEIGHT
```

- **`STALENESS_WEIGHT = 0.05`** (env: `STALENESS_WEIGHT`)
- Fresh content (`recency_score ≈ 1.0`) → penalty ≈ 0
- Stale content (`recency_score ≈ 0.0`) → penalty ≈ 0.05

#### `source_dominance_penalty`

Prevents any single source from monopolising the ranked window.

```python
share = source_count_in_window / window_size
if share > MAX_SOURCE_PCT:
    excess = (share - MAX_SOURCE_PCT) / (1 - MAX_SOURCE_PCT)
    penalty = excess × DOMINANCE_WEIGHT
```

- **`MAX_SOURCE_PCT = 0.30`** (env: `DOMINANCE_MAX_SOURCE_PCT`) — sources
  below 30% share receive zero penalty.
- **`DOMINANCE_WEIGHT = 0.10`** (env: `DOMINANCE_WEIGHT`) — maximum penalty
  equals the maximum interest boost, keeping the two signals balanced.
- Does **not** hard-block any source; `diversity_mixer` handles hard source caps.

### User Category Selections

Declared interests are stored in the `user_category_selections` table
(distinct from the learned `user_preferences` table):

| column | type | description |
|--------|------|-------------|
| `device_id` | `VARCHAR` (FK) | Identifies the user (no PII) |
| `selected_categories` | `JSONB` | Ordered list e.g. `["AI", "Security"]` |
| `created_at` / `updated_at` | `TIMESTAMP` | Audit columns |

**API**

```
GET  /api/v1/users/{device_id}/categories   → selected_categories list
PUT  /api/v1/users/{device_id}/categories   → replace selected_categories
```

### Decay Over Time

As a user engages with the app, the `PersonalizationService` accumulates
learned preference weights in `user_preferences`.  The total of these weights
is used to compute `engagement_decay_factor`:

```
engagement_decay_factor = min(1.0, total_learned_weight / DECAY_SATURATION)
```

where `DECAY_SATURATION = 50.0` (env: `INTEREST_DECAY_SATURATION`).

*New user*: `total_learned_weight ≈ 0` → full interest_boost applies.  
*Active user*: after sustained engagement `total_learned_weight → 50` → the
onboarding selection is fully overridden by observed behaviour.

### Architecture Notes

- The device-agnostic feed is still fetched from **Redis cache** (45 s TTL);
  the personalised re-rank runs **in memory on the cached result**, so the
  cache is shared across all users.
- The ranking modules live in `app/ranking/` alongside the existing scoring
  components:

  | file | responsibility |
  |------|---------------|
  | `interest.py` | `compute_interest_boost()`, `compute_engagement_decay_factor()` |
  | `staleness.py` | `compute_staleness_decay()` |
  | `dominance.py` | `compute_source_dominance_penalty()`, `build_source_counts()` |
  | `feed_score.py` | `compute_feed_score()`, `rerank_feed()`, `explain_feed_score()` |

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
