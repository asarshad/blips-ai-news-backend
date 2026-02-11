# Blips — Task Checklist

Tracking document for pending improvements across backend and mobile.

---

## 1. Conversation Starters: Eliminate Separate API Call

**Problem**: Starters are generated on-demand via `/api/v1/starters/{id}`, causing a visible "Thinking..." loading state in the app. The inline field in feed responses is always `{}` because starters aren't pre-generated.

**Goal**: Generate starters during ingestion so they're returned inline in feed responses. No more separate API call from the mobile app.

### Backend (`blips-ai-news-backend`)

- [x] Add `_generate_starters()` to `IngestionPipeline` in `app/ingestion/service.py`
- [x] Call `_generate_starters()` after article ingestion (after commit/clustering)
- [x] Call `_generate_starters()` after video ingestion (skip for REEL type)
- [x] Add backfill pass in `app/scheduler/tasks_ai_retry.py` — generates starters for items that have summaries but no starters (20 items/run)
- [ ] Deploy backend to Render and verify starters appear in `/api/v1/articles/recent` and `/api/v1/videos/recent` responses
- [ ] Run one-time SQL to backfill starters for existing high-traffic items:
  ```sql
  -- Check how many items lack starters
  SELECT type, COUNT(*) FROM content_items
  WHERE ai_processed = true AND conversation_starters IS NULL
  GROUP BY type;
  ```
  The scheduled `ai_retry` task will auto-backfill 20 items per run.

### Mobile (`blips-mobile`)

- [x] Remove API fallback from `FloatingChatBubbles` — use inline starters or static defaults only
- [x] Remove imports of `starters_repository.dart` and `starters_providers.dart` from bubbles widget
- [x] Remove `_getContentId()`, `_buildLoadingState()`, and `_LoadingBubble` widget
- [x] Add static `_defaultFallbackStarters` constant for when inline data is unavailable
- [x] Update widget tests (`floating_chat_bubbles_test.dart`) — remove mock provider overrides, test inline/defaults
- [ ] Consider deleting `starters_repository.dart` and `starters_providers.dart` (now dead code) after confirming nothing else imports them
- [ ] Deploy to simulator and verify: tap chat button → bubbles appear instantly, no "Thinking..."

---

## 2. English-Only Language Gate

**Problem**: Non-English content leaks into the app (e.g., Hardware Canucks en Español). No language detection exists anywhere in the pipeline. Spanish videos appear with Spanish title/video but English AI summary.

**Goal**: Add lightweight language detection during ingestion. Reject non-English content before it enters the database.

### Backend

- [ ] Add `langdetect>=1.0.9` to `requirements.txt`
- [ ] Create `app/ingestion/language_filter.py`:
  - `is_english(title, description=None) -> bool`
  - Uses `langdetect.detect()` on concatenated title + first 500 chars of description
  - Returns `True` if language is `"en"`, `False` otherwise
  - Returns `True` on detection failure (safe default for curated English sources)
  - Logs non-English detections at WARNING level with detected language and title
- [ ] Insert language gate in `app/ingestion/service.py` — `ingest_rss_entry()`:
  - After dedupe_key check (~L106), before AI summarization (~L108)
  - `if not is_english(entry.title, entry.content): return None`
- [ ] Insert language gate in `app/ingestion/checkpoint_worker.py` — `_add_entry()` for YouTube:
  - After `want_reel != is_reel` filter (~L319), before URL normalization (~L320)
  - `if not is_english(e.title, e.summary): skipped_reasons["non_english"] += 1; return`
- [ ] Insert language gate in RSS branch of `checkpoint_worker.py` (~L168):
  - After cursor tracking, before building values dict
- [ ] Write unit tests in `tests/unit/ingestion/test_language_filter.py`:
  - English titles → True
  - Spanish title → False
  - Mixed/mostly English → True
  - Empty/very short text → True (safe default)
- [ ] Deploy and monitor logs for "non-English" skip messages
- [ ] One-time cleanup of existing non-English content:
  ```sql
  UPDATE content_items SET is_suppressed = true
  WHERE title ILIKE '%en español%' OR title ILIKE '%español%';
  ```

---

## 3. Content Source Revamp

**Problem**: Current source selection has demographic gaps (no female creators, no mobile-focused), missing high-growth categories (AI tools, product launches), and includes dead/low-value sources.

**Goal**: Broader appeal, better AI/mobile/lifestyle coverage, more diverse creator roster.

### RSS Feeds — Add (in `app/integrations/rss_feeds.py`)

- [ ] 9to5Mac — `https://9to5mac.com/feed/` (BREAKING, PREMIUM, cap 3)
- [ ] 9to5Google — `https://9to5google.com/feed/` (BREAKING, PREMIUM, cap 3)
- [ ] Android Authority — `https://www.androidauthority.com/feed/` (ANALYSIS, STANDARD, cap 2)
- [ ] The Information — `https://www.theinformation.com/feed` (ANALYSIS, PREMIUM, cap 1)
- [ ] Tom's Hardware — `https://www.tomshardware.com/feeds/all` (ANALYSIS, STANDARD, cap 2)
- [ ] Product Hunt — `https://www.producthunt.com/feed` (DEV, STANDARD, cap 3)
- [ ] Simon Willison's Blog — `https://simonwillison.net/atom/everything/` (ANALYSIS, PREMIUM, cap 1)
- [ ] The Batch (Andrew Ng) — `https://www.deeplearning.ai/the-batch/feed/` (ANALYSIS, PREMIUM, cap 1)
- [ ] XDA Developers — `https://www.xda-developers.com/feed/` (DEV, STANDARD, cap 2)
- [ ] Digital Trends — `https://www.digitaltrends.com/feed/` (BREAKING, STANDARD, cap 2)
- [ ] MacRumors — `https://feeds.macrumors.com/MacRumors-All` (BREAKING, STANDARD, cap 2)
- [ ] TechRadar — `https://www.techradar.com/rss` (BREAKING, STANDARD, cap 2)

### RSS Feeds — Disable (set `enabled=False`)

- [ ] CSS-Tricks — nearly dead since DigitalOcean acquisition
- [ ] AWS Blog — enterprise noise, low consumer engagement
- [ ] Google Cloud Blog — enterprise announcements
- [ ] OpenAI Blog — covered by TechCrunch/Verge, feed often breaks
- [ ] Apple Newsroom — press releases, covered faster by 9to5Mac/MacRumors

### YouTube Channels — Add (in `app/integrations/youtube_channels.py`)

- [ ] MrMobile (Michael Fisher) — `UCSOpcUkE-is7u7c4AkLgqTw` (EXPLAINER, LONG_FORM, cap 1, PREMIUM)
- [ ] SuperSaf — `UCIrrRLyFMVmmL9NDAU2obJA` (EXPLAINER, MIXED, cap 2, STANDARD)
- [ ] iJustine — `UCey_c7U86mJGz1VJWH5CYPA` (EXPLAINER, LONG_FORM, cap 1, STANDARD)
- [ ] Matt Wolfe — `UCgBFVMhmcGf1sKMV60e2lxg` (EXPLAINER, MIXED, cap 2, PREMIUM)
- [ ] AI Explained — `UCNJ1Ymd5yFuUPtn21xtRbbw` (ENGINEER, LONG_FORM, cap 1, PREMIUM)
- [ ] Flossy Carter — `UCLn62fOJUh43NMlx7b-MieQ` (EXPLAINER, LONG_FORM, cap 1, STANDARD)
- [ ] JerryRigEverything — `UCWFKCr40YwOZQx8FHU_ZqqQ` (EXPLAINER, MIXED, cap 2, STANDARD)
- [ ] Karl Conrad — `UCXJPZ-WyNHliPO3bMqAepOw` (EXPLAINER, MIXED, cap 2, STANDARD)
- [ ] Snazzy Labs — `UCO2x-p9gg9TLKneoj9IZ2uA` (EXPLAINER, MIXED, cap 1, STANDARD)
- [ ] Sam Beckman — `UC_EbMSbyB1PJh5SqXGrHLMg` (EXPLAINER, MIXED, cap 2, STANDARD)

### YouTube Channels — Disable (set `enabled=False`)

- [ ] Ben Eater — educational, publishes ~1/month, not news
- [ ] freeCodeCamp — 3-10 hour tutorials, wrong format
- [ ] NileRed Shorts — science/chemistry, not tech
- [ ] Amazon Web Services (YT) — conference talks
- [ ] NVIDIA (YT) — product demos and conference recordings

### Daily Targets (in `app/core/config.py` or env vars)

- [ ] `DAILY_TARGET_ARTICLES`: 40 → 55
- [ ] `DAILY_TARGET_VIDEOS`: 30 → 35
- [ ] `DAILY_TARGET_REELS`: 30 → 25

### New Feed Role

- [ ] Add `AI` to `FeedRole` enum in `rss_feeds.py`
- [ ] Add `AI` to `ChannelRole` enum in `youtube_channels.py`
- [ ] Apply `AI` role to: Simon Willison, The Batch, Matt Wolfe, AI Explained

### Verification

- [ ] Verify new RSS feed URLs are reachable: `curl -s -o /dev/null -w "%{http_code}" <url>`
- [ ] Verify new YouTube channel IDs return valid RSS: `curl -s "https://www.youtube.com/feeds/videos.xml?channel_id=<id>"`
- [ ] Deploy and wait for one ingestion cycle (30 min)
- [ ] Check `/api/v1/articles/recent` for content from new sources
- [ ] Check `/api/v1/videos/recent` for content from new channels
- [ ] Confirm disabled sources no longer appear in ingestion logs

---

## 4. Future Considerations (from content critique)

Not actionable now, but worth tracking:

- [ ] **Personalization**: Topic preferences, reading history influence on feed ranking
- [ ] **Social proof**: Trending badges, view counts, "X people read this"
- [ ] **Editorial voice**: AI-enhanced context beyond neutral summaries
- [ ] **"Caught up" signal**: Indicate when user has seen all new content
- [ ] **Breaking news push**: Distinguish major announcements from routine content
- [ ] **"AI News" tab/filter**: Leverage new `AI` role for dedicated AI content surface
