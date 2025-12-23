# Curation System Documentation

## Overview

The Blips Curation System is a production-quality personalization engine that delivers ranked, diverse content feeds to mobile users. It implements a signal-driven approach where user interactions inform content ranking without making AI calls during feed delivery.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Client (Mobile App)                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          API Layer                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐       │
│  │ GET /playlist    │  │ POST /interactions│  │ GET /preferences │       │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Service Layer                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ PlaylistSvc  │  │ PersonalSvc  │  │ ScoringService│  │ ClusterSvc │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       Repository Layer                                   │
│         ┌──────────────┐              ┌──────────────┐                   │
│         │ ContentRepo  │              │  UserRepo    │                   │
│         └──────────────┘              └──────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                              ▼
              ┌──────────────┐              ┌──────────────┐
              │  PostgreSQL  │              │    Redis     │
              │   (Source)   │              │   (Cache)    │
              └──────────────┘              └──────────────┘
```

## Data Models

### content_items
Unified content table for articles, videos, and reels.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| type | ENUM | ARTICLE, VIDEO, REEL |
| source | VARCHAR | Source name (e.g., "TechCrunch") |
| source_url | VARCHAR | Original URL |
| published_at | TIMESTAMP | Publish time |
| title | VARCHAR | Content title |
| description | TEXT | Short description |
| summary | TEXT | AI-generated summary (articles/videos only) |
| image_url | VARCHAR | Thumbnail/hero image |
| video_url | VARCHAR | Video embed URL (videos/reels) |
| duration | INTEGER | Duration in seconds |
| topics | JSONB | Extracted topics |
| entities | JSONB | Named entities (companies, people, products) |
| quality_score | FLOAT | Source quality + completeness |
| trend_score | FLOAT | Cluster size + engagement |
| recency_score | FLOAT | Exponential time decay |
| diversity_boost | FLOAT | Topic balance adjustment |
| global_score | FLOAT | Combined ranking score |
| cluster_id | VARCHAR | Cluster identifier (implicit) |
| is_cluster_canonical | BOOLEAN | Best representative of cluster |
| dedupe_key | VARCHAR | Deduplication hash |

> **Note**: Clusters are implicit via the `cluster_id` field. There is no separate `content_clusters` table.
> Cluster metadata (item count, primary topic) is computed on-the-fly via aggregation queries.

### user_profiles
Tracks anonymous users by device ID.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| device_id | VARCHAR | Anonymous device identifier |
| created_at | TIMESTAMP | First seen |
| last_active | TIMESTAMP | Last interaction |

### user_preferences
Learned user preferences with time decay.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| user_id | FK | User profile reference |
| pref_type | ENUM | TOPIC, ENTITY, SOURCE, FORMAT |
| pref_key | VARCHAR | Preference value (e.g., "ai", "openai") |
| weight | FLOAT | Accumulated weight |
| updated_at | TIMESTAMP | Last update |

### interaction_events
Append-only log of user interactions.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| user_id | FK | User profile reference |
| content_item_id | FK | Content reference |
| event_type | ENUM | VIEW_10S, OPEN_SOURCE, SHARE, SAVE, CHAT_START, CHAT_MESSAGE |
| timestamp | TIMESTAMP | Event time |


## Scoring Algorithm

### Global Score Formula
```
global_score = 0.40 * quality + 0.30 * trend + 0.20 * recency + 0.10 * diversity
```

### Quality Score (40%)
- **Source weight (50%)**: Predefined weights per source (0-1 scale)
- **Content completeness (30%)**: Title, description, summary, images
- **Metadata richness (20%)**: Topics and entities extracted

### Trend Score (30%)
- **Cluster size (80%)**: Logarithmic scaling of coverage
- **Engagement (20%)**: Weighted sum of user interactions (capped to prevent gaming)

### Recency Score (20%)
```
recency = 2^(-age_hours / 24)
```
- 0 hours: 1.0
- 24 hours: 0.5
- 48 hours: 0.25
- 72 hours: 0.125

### Diversity Boost (10%)
- Overrepresented topics (>40%): Penalty up to -0.5
- Underrepresented topics (<10%): Boost up to +0.5

## Personalization

### Event Weights
| Event | Weight | Signal |
|-------|--------|--------|
| VIEW_10S | +1 | Basic interest |
| OPEN_SOURCE | +3 | Strong interest |
| SHARE | +5 | Endorsement |
| SAVE | +5 | Value for later |
| CHAT_START | +6 | Engagement intent |
| CHAT_MESSAGE | +1 | Continued engagement (capped at 10/content) |

### Preference Decay
- Daily decay factor: 0.95
- Preferences below 0.1 are pruned
- Maximum 100 preferences per type

### Personalization Score
Weights are configurable via environment variables:
```
personalization = PERSONALIZATION_TOPIC_WEIGHT * topic_match + 
                  PERSONALIZATION_ENTITY_WEIGHT * entity_match + 
                  PERSONALIZATION_SOURCE_WEIGHT * source_match + 
                  PERSONALIZATION_FORMAT_WEIGHT * format_match
```

Default values: 0.35 topic, 0.30 entity, 0.20 source, 0.15 format

## Clustering

### Similarity Metrics
- **Entity overlap (40%)**: Jaccard similarity of named entities
- **Title similarity (40%)**: Character-level token overlap
- **Topic overlap (20%)**: Jaccard similarity of topics

### Configuration
- Cluster window: 48 hours
- Combined threshold: 0.5
- Time proximity bonus: Items within 2 hours get 5% boost

### Canonical Selection
The item with highest `global_score` per cluster+type combination is marked canonical.

## API Endpoints

### GET /session/playlist
Get personalized content playlist. Uses session-based snapshots with cursor pagination.

**Headers:**
- `X-Device-ID`: Required device identifier

**Query Parameters:**
- `type`: ARTICLE | VIDEO | REEL (required)
- `size`: 10-100 (default: 50)
- `session_id`: Session UUID (optional, for cursor pagination)
- `cursor`: Pagination cursor (optional, for continuing session)
- `refresh`: Force regenerate playlist (default: false)

**Response:**
```json
{
  "items": [
    {
      "id": 123,
      "type": "ARTICLE",
      "source": "TechCrunch",
      "source_url": "https://...",
      "title": "...",
      "summary": "...",
      "topics": ["ai", "openai"],
      "entities": ["openai", "gpt-5"],
      "published_at": "2024-01-15T10:30:00Z",
      "global_score": 0.85
    }
  ],
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "cursor": "abc123",
  "total_items": 50,
  "has_more": true
}
```

> **Session-Based Pagination**: Playlists are session snapshots. Use `session_id` and `cursor` 
> from the response to continue fetching more items. This ensures stable ordering during 
> swipe feeds (no shifting items on re-request).

### POST /session/interactions
Record user interaction.

**Headers:**
- `X-Device-ID`: Required device identifier

**Body:**
```json
{
  "content_item_id": 123,
  "event_type": "VIEW_10S",
  "extra_data": {}
}
```

### GET /session/preferences
Get user's learned preferences (debug).

### GET /session/stats
Get user statistics (debug).

### GET /session/playlist-stats
Get playlist generation statistics (admin).

## Scheduled Tasks

| Task | Schedule | Description |
|------|----------|-------------|
| `fetch_and_process_news` | Every 3 hours | Fetch articles/videos, run ingestion |
| `run_scoring_job` | Hourly | Update all content scores |
| `run_clustering_job` | Every 15 min | Cluster unclustered content |
| `run_preference_decay_job` | Daily 3 AM | Apply preference decay |

## Configuration

### Environment Variables
```bash
# Curation settings
PLAYLIST_CACHE_TTL_SECONDS=300
DEFAULT_PLAYLIST_SIZE=50
MAX_CONTENT_AGE_HOURS=72
RECENCY_HALF_LIFE_HOURS=24
MAX_TOPIC_DOMINANCE=0.40
PREFERENCE_DECAY_FACTOR=0.95

# Clustering settings
CLUSTER_WINDOW_HOURS=48
MIN_CLUSTER_SIMILARITY=0.5

# Personalization weights (must sum to 1.0)
PERSONALIZATION_TOPIC_WEIGHT=0.35
PERSONALIZATION_ENTITY_WEIGHT=0.30
PERSONALIZATION_SOURCE_WEIGHT=0.20
PERSONALIZATION_FORMAT_WEIGHT=0.15

# Trend score weights (must sum to 1.0)
TREND_CLUSTER_WEIGHT=0.80
TREND_ENGAGEMENT_WEIGHT=0.20   # Capped to prevent gaming

# Session settings
SESSION_SNAPSHOT_TTL_SECONDS=3600
```

## Database Migration

Run the migration to create curation tables:

```bash
cd src/backend
alembic upgrade head
```

To backfill existing articles/videos:

```bash
python -c "from app.scheduler.tasks import run_backfill_job; run_backfill_job()"
```

## Testing

Run unit tests:

```bash
cd src/backend
pytest tests/test_curation.py -v
```

## Files Structure

```
app/
├── models/
│   └── content.py          # SQLAlchemy models
├── repositories/
│   ├── content_repo.py     # Content repo (with cluster aggregation methods)
│   └── user_repo.py        # User, preference, event repos
├── services/
│   ├── clustering_service.py    # Story clustering
│   ├── scoring_service.py       # Content scoring
│   ├── personalization_service.py # Preference learning
│   ├── playlist_service.py      # Playlist generation (session snapshots)
│   └── ingestion_pipeline.py    # Content ingestion
├── api/routes/
│   └── session.py          # API endpoints
├── scheduler/
│   ├── __init__.py         # Scheduler setup
│   └── tasks.py            # Background tasks
└── core/
    └── config.py           # Configuration (including weight coefficients)

alembic/versions/
├── curation_system_001.py        # Create curation tables
└── curation_system_002_drop_clusters.py  # Drop content_clusters table

tests/
└── test_curation.py        # Unit tests
```

## Performance Considerations

1. **Redis Caching**: Playlists cached for 5 minutes per user/type
2. **Precomputed Scores**: All scores computed hourly, not at request time
3. **Indexed Queries**: Key columns indexed for fast lookups
4. **Batch Operations**: Scoring and clustering run in batches
5. **Connection Pooling**: SQLAlchemy session management

## Future Improvements

1. **ML-based topic extraction**: Replace keyword matching with NLP
2. **Collaborative filtering**: Cross-user preference signals
3. **A/B testing framework**: Compare ranking algorithms
4. **Real-time scoring**: Event-driven score updates
5. **Embedding-based clustering**: Semantic similarity matching
