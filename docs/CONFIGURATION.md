# Configuration Reference

This document lists all configuration options for both the backend and mobile app, explains what they do, and warns about dangerous values.

---

## Backend Configuration

All backend configuration is in environment variables. Set them in `.env` file or via Docker environment.

### Database Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/blips` | PostgreSQL connection string |
| `DATABASE_POOL_SIZE` | `5` | Connection pool size |
| `DATABASE_MAX_OVERFLOW` | `10` | Extra connections allowed |
| `DATABASE_POOL_TIMEOUT` | `30` | Seconds to wait for connection |

**Safe values**: Pool size 5-20, overflow 10-30
**Dangerous**: Pool size > 50 (exhausts DB connections)

### Redis Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |

**Safe values**: Use database 0-15
**Dangerous**: Using production Redis without auth

### OpenAI Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | `` (required) | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for summarization |
| `OPENAI_MAX_TOKENS` | `300` | Max tokens per summary |
| `OPENAI_TEMPERATURE` | `0.7` | Creativity (0-1) |

**Safe values**: 
- Model: `gpt-4o-mini` (cheap), `gpt-4o` (better quality)
- Max tokens: 200-500
- Temperature: 0.5-0.8

**Dangerous**: 
- Temperature > 0.9 (unpredictable outputs)
- Max tokens > 1000 (expensive, summaries too long)

### Scheduler Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULER_NEWS_FETCH_INTERVAL_MINUTES` | `30` | Minutes between feed fetches |
| `SCHEDULER_SCORING_INTERVAL_MINUTES` | `60` | Minutes between score updates |
| `SCHEDULER_CLUSTERING_INTERVAL_MINUTES` | `15` | Minutes between clustering runs |
| `SCHEDULER_PREFERENCE_DECAY_INTERVAL_HOURS` | `24` | Hours between preference decay |

**Safe values**:
- News fetch: 15-60 minutes
- Scoring: 30-120 minutes
- Clustering: 10-30 minutes

**Dangerous**:
- News fetch < 10 min (may hit API rate limits)
- Scoring < 15 min (database load)
- Clustering < 5 min (CPU intensive)

### Quota Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `QUOTA_MAX_MESSAGES_PER_DAY` | `5` | AI chat messages per user per day |
| `QUOTA_MAX_MESSAGES_PER_ARTICLE` | `3` | AI chat messages per article |

**Safe values**: 3-10 messages per day
**Dangerous**: > 50 (OpenAI costs will spike)

### Cache Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_ARTICLE_CACHE_COUNT` | `5` | Articles to pre-cache |
| `CACHE_PLAYLIST_CACHE_TTL_SECONDS` | `300` | Playlist cache duration |
| `CACHE_SESSION_SNAPSHOT_TTL_SECONDS` | `3600` | Session cache duration |

**Safe values**:
- Article cache: 3-10
- Playlist TTL: 60-600 seconds

**Dangerous**:
- Playlist TTL < 30 seconds (defeats caching)
- Session TTL > 86400 (stale data)

### Content Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_PLAYLIST_SIZE` | `50` | Items in personalized feed |
| `MAX_CONTENT_AGE_HOURS` | `72` | Max age for content in feeds |

**Safe values**:
- Playlist size: 20-100
- Max age: 24-168 hours

**Dangerous**:
- Playlist size > 200 (slow queries)
- Max age > 168 (stale content)

---

## Clustering Configuration

File: `/app/config/clustering.py`

### Similarity Thresholds

| Setting | Default | Description |
|---------|---------|-------------|
| `window_hours` | `48` | Time window for clustering |
| `min_entity_overlap` | `0.3` | Min entity overlap (0-1) |
| `min_title_similarity` | `0.6` | Min title similarity (0-1) |
| `min_topic_overlap` | `0.4` | Min topic overlap (0-1) |
| `combined_threshold` | `0.5` | Final threshold for clustering |

**Tuning guide**:

```
More aggressive clustering (fewer duplicates, may miss unique angles):
  combined_threshold: 0.4
  min_entity_overlap: 0.2

Less aggressive clustering (more items, more duplicates):
  combined_threshold: 0.6
  min_entity_overlap: 0.4
```

### Similarity Weights

| Setting | Default | Description |
|---------|---------|-------------|
| `entity_weight` | `0.40` | Weight for entity overlap |
| `title_weight` | `0.40` | Weight for title similarity |
| `topic_weight` | `0.20` | Weight for topic overlap |

**Must sum to 1.0**

**Tuning guide**:
- Increase `entity_weight` if same entities = same story
- Increase `title_weight` if titles are reliable
- Increase `topic_weight` if different sources use different words

---

## Ranking Configuration

File: `/app/config/ranking.py`

### Score Weights

| Setting | Default | Description |
|---------|---------|-------------|
| `quality_weight` | `0.25` | Weight for content quality |
| `recency_weight` | `0.35` | Weight for freshness |
| `trend_weight` | `0.25` | Weight for engagement |
| `diversity_weight` | `0.15` | Weight for topic balance |

**Must sum to 1.0**

**Tuning guide**:

```
For breaking news emphasis:
  recency_weight: 0.45
  trend_weight: 0.30
  quality_weight: 0.15
  diversity_weight: 0.10

For quality emphasis:
  quality_weight: 0.40
  recency_weight: 0.25
  trend_weight: 0.20
  diversity_weight: 0.15
```

### Recency Decay

| Setting | Default | Description |
|---------|---------|-------------|
| `recency_half_life_hours` | `12` | Half-life for time decay |

**Tuning guide**:
- 6 hours: Very aggressive, old news dies fast
- 12 hours: Balanced (default)
- 24 hours: Slow decay, older content stays relevant

---

## Mobile Configuration

### Video Player Pool

File: `/lib/features/feed/providers/video/video_config.dart`

| Setting | Default | Description |
|---------|---------|-------------|
| `poolSize` | `5` | Number of video players |
| `preloadCount` | `2` | Videos to preload ahead |
| `disposeThreshold` | `3` | Videos behind before cleanup |
| `metricsHistorySize` | `20` | Performance samples to keep |

**Safe values**:
- Pool size: 3-7
- Preload count: 1-3

**Dangerous**:
- Pool size > 7 (memory issues)
- Pool size < 3 (poor scroll performance)
- Preload count > pool size (impossible)

### API Configuration

File: `/lib/core/api/api_client.dart`

| Setting | Default | Description |
|---------|---------|-------------|
| `baseUrl` | `http://localhost:8000/api/v1` | Backend API URL |
| `connectTimeout` | `10000` | Connection timeout (ms) |
| `receiveTimeout` | `30000` | Response timeout (ms) |

**Production values**:
```dart
baseUrl: 'https://api.blips.app/api/v1'
connectTimeout: 15000
receiveTimeout: 60000
```

---

## Docker Configuration

File: `docker-compose.yml`

### Service Ports

| Service | Internal | External | Description |
|---------|----------|----------|-------------|
| `api` | `8000` | `8000` | FastAPI server |
| `db` | `5432` | `5432` | PostgreSQL |
| `redis` | `6379` | `6379` | Redis |

**Production**: Don't expose db/redis ports externally.

### Resource Limits

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
```

**Safe values**:
- API: 256M-1G
- PostgreSQL: 256M-2G
- Redis: 64M-256M

---

## Environment-Specific Overrides

### Development

```env
DEBUG=true
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/blips_dev
OPENAI_API_KEY=sk-dev-key
SCHEDULER_NEWS_FETCH_INTERVAL_MINUTES=60
```

### Staging

```env
DEBUG=false
DATABASE_URL=postgresql://user:pass@staging-db:5432/blips
OPENAI_API_KEY=sk-staging-key
SCHEDULER_NEWS_FETCH_INTERVAL_MINUTES=30
```

### Production

```env
DEBUG=false
DATABASE_URL=postgresql://user:pass@prod-db:5432/blips
OPENAI_API_KEY=sk-prod-key
SCHEDULER_NEWS_FETCH_INTERVAL_MINUTES=15

# Tighter quotas
QUOTA_MAX_MESSAGES_PER_DAY=3
CACHE_PLAYLIST_CACHE_TTL_SECONDS=600
```

---

## Common Mistakes

### 1. Missing OpenAI Key

**Symptom**: Articles have no summaries, chat doesn't work
**Fix**: Set `OPENAI_API_KEY` in environment

### 2. Wrong Database URL

**Symptom**: "Connection refused" errors
**Fix**: 
- Local dev: `localhost:5432`
- Docker: `db:5432` (service name)

### 3. Redis Not Running

**Symptom**: Caching doesn't work, slow responses
**Fix**: Ensure Redis container is running

### 4. Pool Size Too Small

**Symptom**: "Connection pool exhausted" errors
**Fix**: Increase `DATABASE_POOL_SIZE` or reduce concurrent requests

### 5. Video Pool Too Large

**Symptom**: App crashes on low-memory devices
**Fix**: Reduce `poolSize` to 3-4
