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
│   /api/v1/interactions        - Log user engagement                 │
│   /api/v1/chat                - AI chat about articles              │
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
