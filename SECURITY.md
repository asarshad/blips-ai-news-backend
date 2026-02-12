# Blips — Security & Non-Functional Requirements

Tracking document for security hardening, privacy compliance, and production readiness.  
Format mirrors [TASKS.md](TASKS.md).

---

## 1. Admin & Debug Endpoints: Authentication Gate

**Problem**: `/api/v1/admin/*` (6 routes) and `/api/v1/debug/*` (4 routes) are publicly accessible with zero authentication. Anyone can toggle feature flags, trigger ingestion, dump DB stats, invalidate cache, and trigger AI processing. Both files have TODO comments acknowledging this.

**Severity**: CRITICAL

**Goal**: Protect admin/debug routes with a shared secret (`ADMIN_API_KEY` env var). Optionally disable debug routes entirely in production.

### Backend

- [ ] Add `ADMIN_API_KEY: str = ""` to `app/core/config.py`
- [ ] Add `DEBUG_ROUTES_ENABLED: bool = False` to `app/core/config.py`
- [ ] Create `app/core/auth.py` with `require_admin_key` FastAPI dependency:
  - Reads `X-Admin-Key` header
  - Compares against `ADMIN_API_KEY` using `secrets.compare_digest`
  - Returns 401 if missing, 403 if wrong
  - If `ADMIN_API_KEY` is empty/unset → reject all requests (fail-closed)
- [ ] Add `require_admin_key` dependency to all 6 admin routes in `admin.py`
- [ ] Add `require_admin_key` dependency to all 4 debug routes in `debug.py`
- [ ] Conditionally register debug router in `api/__init__.py` based on `DEBUG_ROUTES_ENABLED`
- [ ] Add `ADMIN_API_KEY` and `DEBUG_ROUTES_ENABLED` to `.env.example`
- [ ] Set `ADMIN_API_KEY` in Render environment variables
- [ ] Update OpenAPI snapshot after adding auth headers
- [ ] Unit test: request without key → 401
- [ ] Unit test: request with wrong key → 403
- [ ] Unit test: request with correct key → 200
- [ ] Unit test: debug routes return 404 when `DEBUG_ROUTES_ENABLED=false`

---

## 2. CORS Lockdown

**Problem**: CORS is `allow_origins=["*"]` with `allow_credentials=True` in `main.py` L280-285. Any website can make credentialed cross-origin requests to the API. Per browser spec, wildcard + credentials should be rejected, but it signals zero origin restriction effort.

**Severity**: HIGH

**Goal**: Restrict CORS to known origins. Mobile app uses direct HTTP (no CORS needed). Only admin dashboard or local dev needs CORS.

### Backend

- [ ] Add `CORS_ORIGINS: str = ""` to `app/core/config.py` (comma-separated)
- [ ] Update CORS middleware in `main.py`:
  - If `CORS_ORIGINS` is set → parse as list, use as `allow_origins`
  - If empty → use `["capacitor://localhost", "http://localhost"]`
  - Remove `allow_credentials=True` (not needed for API-key auth)
- [ ] Add `CORS_ORIGINS` to `.env.example`

---

## 3. HTTP Rate Limiting

**Problem**: No request-level rate limiting. No `slowapi` or equivalent in `requirements.txt`. All endpoints are vulnerable to abuse — brute force, scraping, resource exhaustion. Chat endpoint has daily quota but no per-minute throttle.

**Severity**: HIGH

**Goal**: Add per-IP rate limiting using `slowapi` (standard FastAPI solution).

### Backend

- [ ] Add `slowapi>=0.1.9` to `requirements.txt`
- [ ] Add rate limiter setup in `main.py`:
  - Default: 60 req/min per IP for feed/content endpoints
  - Chat: 10 req/min per IP (on top of existing daily quota)
  - Admin: 10 req/min per IP
- [ ] Add `RATE_LIMIT_DEFAULT: str = "60/minute"` and `RATE_LIMIT_CHAT: str = "10/minute"` to config
- [ ] Handle `429 Too Many Requests` response (custom JSON body)

### Mobile

- [ ] Handle 429 responses gracefully — show "Please wait" snackbar instead of generic error

---

## 4. Device ID: Stop Storing Raw IP Addresses

**Problem**: `_get_device_id()` in both `ai_chat.py` (L27-30) and `usage.py` (L17-20) stores `client_ip + user_agent[:50]` as `device_id` — raw IP is PII under GDPR/CCPA. A third scheme in `session.py` (L146-154) uses client-supplied `X-Device-ID` header. Three different approaches, two of which embed plaintext IP.

**Severity**: HIGH

**Goal**: Unify on hashed device identifiers. Never store raw IP addresses.

### Backend

- [ ] Create `app/core/device_id.py`:
  - `hash_device_id(ip: str, user_agent: str) -> str` — SHA-256, truncated to 32 hex chars
  - Deterministic: same IP+UA → same hash
  - One-way: cannot recover IP from hash
- [ ] Update `_get_device_id()` in `ai_chat.py` to use `hash_device_id()`
- [ ] Update `_get_device_id()` in `usage.py` to use `hash_device_id()`
- [ ] Extract duplicated `_get_device_id()` into the shared `device_id.py` module (DRY)
- [ ] Consider aligning session.py to accept either `X-Device-ID` or fall back to hashed IP+UA
- [ ] Add Alembic migration to hash existing `device_id` values in `usage` / `interaction_events` tables

---

## 5. Content Scraping: RSS-Only Mode

**Problem**: `_extract_article_content` in `rss_client.py` scrapes full article pages (up to 8,000 chars of body text) using spoofed User-Agent headers from 3 rotating browser strings. This republishes copyrighted content, violates site ToS, and has no SSRF protection (arbitrary URLs from RSS feeds are fetched server-side with no private-IP blocklist).

**Severity**: CRITICAL (copyright + SSRF)

**Goal**: Use only content provided by RSS feeds (description, summary, content:encoded). Stop scraping article pages entirely. RSS description + AI summarization is sufficient.

### Backend

- [ ] Refactor `_extract_article_content` in `rss_client.py`:
  - Remove full-page scraping logic (`requests.get` + BeautifulSoup extraction)
  - Remove `SCRAPE_BLOCKLIST` (no longer needed when not scraping)
  - Remove `USER_AGENTS` rotation list
  - Keep only RSS feed content (`description`, `summary`, `content:encoded`) as source
- [ ] Keep `_extract_image_url()` for RSS metadata images but remove any fallback that fetches article pages for OG images
- [ ] If RSS description is empty/too short → log warning and skip article (don't scrape)
- [ ] Verify all 32 enabled feeds provide usable `<description>` or `<content:encoded>`
- [ ] Test: ingestion still works with RSS-only content
- [ ] Test: AI summarization quality is acceptable with RSS descriptions

---

## 6. YouTube: Replace Unofficial Libraries

**Problem**: `youtube-transcript-api` (backend, `youtube_client.py` L16) scrapes YouTube's internal endpoints for transcripts. `youtube_explode_dart` (mobile, `youtube_resolver.dart`) reverse-engineers stream URLs using TV + iOS API clients to bypass blocks. Both violate YouTube ToS and risk DMCA takedowns. Mobile already has `youtube_player_flutter` (iframe-based, official) as a dependency alongside `youtube_explode_dart`.

**Severity**: HIGH

**Goal**: Replace unofficial YouTube access with compliant alternatives.

### Backend

- [ ] Remove `youtube-transcript-api==0.6.2` from `requirements.txt`
- [ ] Remove transcript imports and `get_transcript` logic from `youtube_client.py`
- [ ] For video summaries, rely on: RSS feed description → YouTube Data API snippet → channel-provided description
- [ ] Keep YouTube Data API usage for duration lookups (legitimate API usage)

### Mobile

- [ ] Migrate video playback from `youtube_explode_dart` + `video_player` to `youtube_player_flutter` (already a dependency — uses official iframe/webview)
- [ ] Remove `youtube_explode_dart` from `pubspec.yaml`
- [ ] Remove `youtube_resolver.dart` or refactor to use `youtube_player_flutter`
- [ ] Update video feed cards to use the iframe player
- [ ] Test video playback across iOS and Android
- [ ] Verify inline playback works in swipe feed

### Decision needed

- `youtube_player_flutter` (iframe) is already in `pubspec.yaml`. Confirm it supports inline playback in the feed before removing `youtube_explode_dart`.

---

## 7. Privacy Policy & Terms: Update to Match Reality

**Problem**: Privacy policy (`blips-site/privacy.html`) doesn't disclose IP collection, behavioral tracking (`interaction_events` table), AI chat processing by third-party LLMs (Mistral/OpenAI), or content scraping. Footer says *"This policy is a starting point."* Effective date set to January 2026. Terms has similar template quality.

**Severity**: HIGH

**Goal**: Accurate privacy policy and terms of service that match actual data practices.

### Site (`blips-site`)

- [ ] Rewrite `privacy.html` to disclose:
  - Device identifiers collected (hashed IP+UA — after fix #4)
  - Interaction tracking: what events, how used (personalization), retention period
  - AI chat: messages processed by Mistral AI / OpenAI, retention policy
  - Content sources: aggregated from RSS feeds and YouTube with attribution
  - Third-party services: Mistral AI, OpenAI, YouTube Data API
  - Data retention: specific periods (see #8)
  - Deletion: functional mechanism (not just "email us")
  - No cookies, no advertising, no sale of data
- [ ] Rewrite `terms.html`:
  - AI-generated content disclaimer (summaries may be inaccurate)
  - Third-party content attribution
  - User responsibility for chat interactions
  - Remove "template/starting point" disclaimer
- [ ] Add minimum age requirement (13+) to both documents
- [ ] Update effective dates to current date
- [ ] Remove the "starting point" disclaimer footer

---

## 8. Data Retention & Deletion

**Problem**: All data grows indefinitely — usage records, interaction events, conversations, user profiles. No deletion mechanism exists despite privacy policy implying one. No scheduled cleanup.

**Severity**: HIGH (GDPR/CCPA)

**Goal**: Implement data retention policies and a working deletion endpoint.

### Backend

- [ ] Define retention periods:
  - `usage` table: 90 days
  - `interaction_events`: 90 days
  - `conversations`: 30 days (or remove if chats are device-local only)
  - `user_profiles` / `user_preferences`: until deletion requested
  - `content_items`: already managed via evergreen window (45 days)
- [ ] Create `app/scheduler/tasks_cleanup.py`:
  - Scheduled daily job to purge expired records
  - Log count of deleted records per table
- [ ] Add `DELETE /api/v1/session/data` endpoint:
  - Accepts `X-Device-ID` header
  - Deletes all data for that device: usage, interaction_events, user_profile, user_preferences
  - Returns confirmation with counts
- [ ] Register cleanup task in scheduler config

### Mobile

- [ ] Add "Delete My Data" button in Settings page:
  - Calls the deletion endpoint
  - Clears local SQLite databases
  - Shows confirmation dialog before proceeding

---

## 9. App Store Privacy Declarations

**Problem**: App Store (Apple) and Play Store (Google) require accurate privacy/data declarations. These need to match actual data practices.

**Severity**: MEDIUM

**Goal**: Prepare accurate data declarations for both stores.

### Mobile

- [ ] Apple App Store Connect — Privacy Nutrition Labels:
  - Data Linked to You: Device ID (hashed)
  - Data Used to Track You: None
  - Data types collected: Usage Data, Identifiers (device ID)
  - Purposes: App Functionality, Personalization
- [ ] Google Play Console — Data Safety:
  - Data collected: Device or other IDs, App interactions
  - Data shared: Chat messages with AI providers (Mistral/OpenAI)
  - Security: Data encrypted in transit (HTTPS), data deletion available
- [ ] Document declarations in `PRIVACY_DECLARATIONS.md`

---

## 10. Swagger/OpenAPI Docs: Disable in Production

**Problem**: Swagger UI (`/docs`) and ReDoc (`/redoc`) are enabled unconditionally in `main.py`, exposing full API schema including admin/debug endpoints to anyone.

**Severity**: LOW

**Goal**: Disable interactive docs in production, keep for development.

### Backend

- [ ] Add `DOCS_ENABLED: bool = False` to `app/core/config.py`
- [ ] Conditionally set `docs_url` and `redoc_url` in `main.py` based on config:
  - `docs_url="/docs" if settings.DOCS_ENABLED else None`
  - `redoc_url="/redoc" if settings.DOCS_ENABLED else None`
- [ ] OpenAPI JSON can stay accessible (needed for contract tests)
- [ ] Add `DOCS_ENABLED=true` to `.env.example` for local development

---

## 11. Content Attribution

**Problem**: Articles and videos are served with source names but image hotlinking uses source bandwidth without attribution.

**Severity**: MEDIUM (copyright best practice)

**Goal**: Ensure every content item clearly links to its original source.

### Mobile

- [ ] Verify every article card has a working "Open in Browser" link to the original article
- [ ] Verify every video card links to the original YouTube video
- [ ] Add source domain display on all cards (already partially done)

### Backend (future)

- [ ] Consider proxying/caching thumbnail images instead of hotlinking
- [ ] Ensure `source_url` is present in all API responses

---

## Priority Order

| Priority | Task | Severity | Effort |
|----------|------|----------|--------|
| **P0** | 1. Admin/Debug auth gate | CRITICAL | Small |
| **P0** | 5. Stop full-page scraping | CRITICAL | Small |
| **P1** | 2. CORS lockdown | HIGH | Tiny |
| **P1** | 4. Hash device IDs | HIGH | Small |
| **P1** | 3. Rate limiting | HIGH | Medium |
| **P1** | 7. Privacy policy rewrite | HIGH | Medium |
| **P1** | 8. Data retention & deletion | HIGH | Medium |
| **P2** | 6. YouTube library replacement | HIGH | Large |
| **P2** | 9. App Store declarations | MEDIUM | Small |
| **P3** | 10. Swagger docs in prod | LOW | Tiny |
| **P3** | 11. Content attribution | MEDIUM | Small |
