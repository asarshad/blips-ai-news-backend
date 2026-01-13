# Migration to Curation System Only

## Overview
Complete migration from dual-schema (legacy articles/videos + curation system) to curation system only.

**Decision**: Remove all legacy tables (articles, videos, tags) and use only content_items table.

**User Requirement**: "No need to migrate the data, just start afresh and our scheduler will populate everything"

## Database Changes

### Migration: `4b89bee76a66_migrate_to_curation_only.py`

**Operations**:
1. Drops foreign key constraints from conversations and usage tables
2. Renames `article_id` → `content_item_id` in both tables
3. Adds new foreign keys pointing to content_items
4. Drops legacy tables:
   - `article_tag` (junction table)
   - `articles`
   - `videos`
   - `tags`

**Note**: Downgrade not supported (destructive migration)

## Code Changes

### Models

**Updated:**
- `app/models/conversation.py` - Changed `article_id` → `content_item_id`, FK to content_items
- `app/models/usage.py` - Changed `article_id` → `content_item_id`, FK to content_items
- `app/models/content.py` - Added `conversations` relationship

**Removed from imports:**
- `app/models/__init__.py` - Removed Article, Tag, Video

### Repositories

**Updated:**
- `app/repositories/conversation_repo.py` - All methods now use `content_item_id`
  - `get_article_messages()` → `get_content_messages()`
  - `get_recent_by_article()` removed
  - `get_articles_with_conversations()` → `get_content_items_with_conversations()`
  
- `app/repositories/usage_repo.py`
  - `get_article_usage()` → `get_content_usage()`
  - `record_usage()` parameter changed to `content_item_id`

**Removed from imports:**
- `app/repositories/__init__.py` - Removed ArticleRepository, VideoRepository

### Schemas

**Updated:**
- `app/schemas/conversation.py` - Changed to use `content_item_id` instead of `article_id/video_id`

### API Routes

**`app/api/routes/conversation.py`:**
- Uses ContentItemRepository instead of ArticleRepository
- All endpoints reference `content_item_id`

**`app/api/routes/ai_chat.py`:**
- Uses ContentItemRepository only
- Removed VideoRepository dependency
- Updated to use `content_item_id`

**`app/api/routes/articles.py`:**
- Removed `/fetch` endpoint (legacy ingestion)
- Removed `/regenerate-summaries` endpoint (legacy)
- Removed `/{article_id}/engage` endpoint (legacy hot score)
- Removed legacy ArticleService dependency
- Kept: `/next`, `/cache`, `/recent`, `/tags`, `/{article_id}` (all use ContentItemRepository)

**`app/api/routes/videos.py`:**
- Removed `/fetch` endpoint (legacy ingestion)
- Removed `/regenerate-summaries` endpoint (legacy)
- Removed `/{video_id}/engage` endpoint (legacy hot score)
- Removed `/` POST endpoint (create video)
- Removed VideoService and VideoRepository dependencies
- Kept: `/recent`, `/reels`, `/{video_id}` (all use ContentItemRepository)

**`app/api/routes/admin.py`:**
- Removed `/trigger/ingestion` endpoint (used legacy ArticleRepository)

**`app/api/routes/usage.py`:**
- Updated parameter `article_id` → `content_item_id`

### Services

**`app/services/ai_chat.py`:**
- Changed to use ContentItemRepository instead of ArticleRepository/VideoRepository
- Updated method signature: `content_item_id` instead of `article_id/video_id`
- Simplified to work with unified content items

**`app/services/quota_manager.py`:**
- Already uses content_item_id internally via UsageRepository

**Legacy services (no longer used, left for potential future reference):**
- `app/services/summarizer.py` - ArticleRepository (not used)
- `app/services/news_fetcher.py` - ArticleRepository (not used)
- `app/services/article_service.py` - ArticleRepository (not used)
- `app/services/video_service.py` - VideoRepository (not used)
- `app/services/video_fetcher.py` - VideoRepository (not used)

### Ingestion & Scheduler

**`app/ingestion/service.py` (IngestionPipeline):**
- `run_backfill()` now returns empty stats with TODO
- Made `article_repo` parameter optional
- Legacy article/video table backfilling removed
- TODO: Implement direct RSS/YouTube fetching

**`app/scheduler/tasks.py`:**
- Removed legacy article fetching (`_process_articles_with_stats`)
- Removed legacy video fetching (`_fetch_videos_with_stats`)
- Removed standalone `fetch_and_process_videos()` task
- `fetch_news_and_articles()` now only calls curation ingestion
- Curation ingestion currently returns empty (awaiting RSS/YouTube implementation)

## Testing Plan

### Local Docker Testing
```bash
cd /Users/aarshad/dev/projects/blips/blips-ai-news-backend/src
docker compose down -v
docker compose up --build
```

**Verify**:
1. ✅ Database migrations run successfully
2. ✅ API starts without errors
3. ✅ Health endpoint responds: `http://localhost:8000/health`
4. ⚠️  Scheduler runs without errors (will log warning about ingestion TODO)
5. ⚠️  No content will be populated yet (RSS/YouTube ingestion not implemented)

### API Endpoints to Test

**Working endpoints (use ContentItemRepository):**
- GET `/api/articles/recent` - Should work once content exists
- GET `/api/articles/next` - Should work once content exists
- GET `/api/articles/cache` - Should work once content exists
- GET `/api/articles/{id}` - Should work for content_items with type=ARTICLE
- GET `/api/videos/recent` - Should work once content exists
- GET `/api/videos/reels` - Should work once content exists
- GET `/api/videos/{id}` - Should work for content_items with type=VIDEO/REEL
- POST `/api/ai-chat/respond` - Should work with content_item_id
- GET `/api/conversations/{content_item_id}` - Should work
- POST `/api/conversations` - Should work
- GET `/api/usage` - Should work

**Removed endpoints (will 404):**
- POST `/api/articles/fetch` - Removed
- POST `/api/articles/regenerate-summaries` - Removed
- POST `/api/articles/{id}/engage` - Removed
- POST `/api/videos/fetch` - Removed
- POST `/api/videos/regenerate-summaries` - Removed
- POST `/api/videos/{id}/engage` - Removed
- POST `/api/videos/` - Removed
- POST `/api/admin/trigger/ingestion` - Removed

## Known Issues & TODO

### 🚨 Critical: Content Ingestion Not Implemented
The scheduler's `fetch_news_and_articles()` task currently does nothing because:
- Legacy article/video fetching removed
- Direct RSS/YouTube ingestion not yet implemented

**TODO**: Implement direct ingestion in `IngestionPipeline.run_backfill()`:
```python
# Pseudocode
def run_backfill():
    # 1. Fetch from RSS feeds
    rss_items = fetch_from_rss_feeds()
    for item in rss_items:
        content_item = ContentItem(
            type=ContentType.ARTICLE,
            source=item.source,
            title=item.title,
            ...
        )
        self.content_repo.save(content_item)
        self.clustering.cluster_new_item(content_item)
        self._update_scores(content_item)
    
    # 2. Fetch from YouTube
    youtube_items = fetch_from_youtube()
    for item in youtube_items:
        content_item = ContentItem(
            type=ContentType.VIDEO,
            ...
        )
        # Similar save/cluster/score
```

### Database Schema

**Current state after migration:**
- ✅ content_items (unified content table)
- ✅ user_profiles
- ✅ user_preferences
- ✅ interaction_events
- ✅ conversations (FK to content_items)
- ✅ usage (FK to content_items)
- ❌ articles (dropped)
- ❌ videos (dropped)
- ❌ tags (dropped)
- ❌ article_tag (dropped)

## Deployment Steps

1. **Local Verification** (REQUIRED before push)
   ```bash
   cd src
   docker compose down -v
   docker compose up --build
   ```
   - Verify API starts
   - Check `/health` endpoint
   - Review logs for errors

2. **Commit Changes**
   ```bash
   git add -A
   git commit -m "Complete migration to curation system only

   - Remove legacy articles/videos/tags tables
   - Update all models, repos, routes to use content_items
   - Simplify architecture to single content table
   - Remove legacy ingestion endpoints
   
   Note: Content ingestion needs RSS/YouTube implementation"
   ```

3. **Push to Render**
   ```bash
   git push origin main
   ```
   - Render will auto-deploy
   - Database migration will run automatically
   - **WARNING**: This is a destructive migration (drops tables)

4. **Monitor Render Deployment**
   - Check build logs
   - Verify migration succeeds
   - Check health endpoint
   - Monitor scheduler logs

## Rollback Plan

**⚠️  NO ROLLBACK POSSIBLE** - Migration is destructive
- Legacy tables will be dropped with all data
- Must restore from database backup if needed
- Do not deploy to production until thoroughly tested locally

## Success Criteria

- [x] All Python files compile without syntax errors
- [ ] Local Docker build succeeds
- [ ] Database migration runs without errors
- [ ] API starts and responds to health check
- [ ] Scheduler runs without critical errors
- [ ] Mobile app can fetch articles/videos (once content exists)
- [ ] AI chat works with content_item_id
- [ ] Conversation history persists correctly

## Next Steps (Post-Deployment)

1. **Implement Direct Ingestion**
   - Create RSS fetcher that writes to content_items
   - Create YouTube fetcher that writes to content_items
   - Update `IngestionPipeline.run_backfill()` to use them

2. **Mobile App Updates**
   - Update API calls to use new endpoint signatures
   - Change `article_id` → `content_item_id` in requests
   - Test all features end-to-end

3. **Clean Up**
   - Remove legacy service files (summarizer, news_fetcher, etc.)
   - Remove legacy repository files (article_repo, video_repo)
   - Remove unused imports and dependencies

4. **Documentation**
   - Update API documentation with new endpoints
   - Document content ingestion architecture
   - Create runbook for monitoring content population
