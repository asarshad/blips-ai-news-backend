# Editorial Control Layer

The Editorial Control Layer provides admin-only capabilities for content curation: viewing, submitting, boosting, suppressing, and auditing content items.

## Architecture

```
app/
├── api/admin/           # HTTP layer (routes + schemas + server-rendered UI)
│   ├── __init__.py      # Exports admin_router, admin_ui_router
│   ├── routes.py        # REST API endpoints (JSON)
│   ├── schemas.py       # Pydantic request/response models
│   └── ui.py            # Server-rendered HTML dashboard (Pico CSS)
├── domain/editorial/    # Business logic
│   └── service.py       # EditorialService (submit, dedupe, domain helpers)
├── repositories/
│   └── editorial_repo.py  # Data access (CRUD, audit log writes)
└── models/
    └── editorial.py     # EditorialAction audit log model
```

## Authentication

All editorial endpoints require the `X-Admin-Key` header.  
The key is validated against the `ADMIN_API_KEY` environment variable using `secrets.compare_digest` (constant-time comparison to prevent timing attacks).

**Fail-closed**: If `ADMIN_API_KEY` is not set, all admin requests are rejected with 401.

## Data Model

### New columns on `content_items`

| Column | Type | Default | Description |
|---|---|---|---|
| `editorial_boost` | Integer | 0 | 0-3 importance level |
| `manual_added` | Boolean | False | Was this item added via admin? |
| `added_by` | Text | null | Actor who added the item |
| `added_at` | DateTime | null | When the item was manually added |
| `last_modified_by` | Text | null | Last actor to modify |
| `last_modified_at` | DateTime | null | Last modification timestamp |

### `editorial_actions` table (audit log)

| Column | Type | Description |
|---|---|---|
| `id` | BigInteger | Primary key |
| `content_id` | BigInteger | FK → content_items.id |
| `action_type` | Text | ADD, BOOST, SUPPRESS, UNSUPPRESS |
| `old_value` | JSONB | Previous state |
| `new_value` | JSONB | New state |
| `actor` | Text | Who performed the action |
| `created_at` | DateTime | When the action occurred |

### Migration

```bash
alembic upgrade head
```

Migration file: `alembic/versions/editorial_control_001.py`

## API Endpoints

All endpoints are prefixed with `/admin/editorial` and require `X-Admin-Key` header.

### List Content

```
GET /admin/editorial/content?day=2024-01-15&type=article&source=techcrunch&suppressed=false&manual_added=true&sort_by=published_at&page=1&page_size=50
```

**Response**: Paginated list of content items with editorial metadata.

### Content Detail

```
GET /admin/editorial/content/{id}
```

**Response**: Full content item details + audit trail.

### Submit URL

```bash
curl -X POST https://YOUR_HOST/admin/editorial/content/submit \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article", "importance_level": 2}'
```

**Behavior**:
1. Normalizes the URL
2. Checks for duplicates (by source_url and canonical_key)
3. If duplicate found and importance_level > existing boost → updates boost
4. If new → creates a stub ContentItem marked `manual_added=True`

**Responses**:
- `200 {"content_id": 42, "duplicate": true, "status": "duplicate_exists"}`
- `200 {"content_id": 42, "duplicate": true, "status": "duplicate_boosted"}`
- `201 {"content_id": 99, "duplicate": false, "status": "created"}`

### Set Boost

```bash
curl -X POST https://YOUR_HOST/admin/editorial/content/42/boost \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"level": 2}'
```

Boost levels: 0 (none), 1 (low), 2 (medium), 3 (high).  
Each level adds `EDITORIAL_BOOST_WEIGHT` (default 0.05) to global_score.  
Max boost (3) adds 0.15 to the [0-1] score — nudges but does not dominate.

### Suppress (Soft Delete)

```bash
curl -X POST https://YOUR_HOST/admin/editorial/content/42/suppress \
  -H "X-Admin-Key: YOUR_KEY"
```

Suppressed content is excluded from all public feed queries. Idempotent.

### Unsuppress

```bash
curl -X POST https://YOUR_HOST/admin/editorial/content/42/unsuppress \
  -H "X-Admin-Key: YOUR_KEY"
```

## Admin UI

A minimal server-rendered HTML dashboard is available at `/admin/ui/`.  
It uses [Pico CSS](https://picocss.com/) from CDN — no build step required.

### Pages

| URL | Description |
|---|---|
| `/admin/ui/` | Content list with filters |
| `/admin/ui/detail/{id}` | Content detail + boost/suppress controls + audit trail |
| `/admin/ui/submit` | URL submission form |

**Auth**: The UI routes check `X-Admin-Key` header or `?key=...` query param.

## Ranking Integration

Editorial boost is integrated into the global score formula:

```
global_score = (
    quality_weight × quality_score +
    trend_weight × trend_score +
    recency_weight × recency_score +
    diversity_weight × diversity_boost
) + editorial_boost × EDITORIAL_BOOST_WEIGHT
```

- `EDITORIAL_BOOST_WEIGHT` defaults to `0.05` (configurable via env var)
- Max boost (3) adds 0.15 to the score
- Score is clamped to `[0, 1.15]`

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_API_KEY` | Yes | — | API key for admin endpoints |
| `EDITORIAL_BOOST_WEIGHT` | No | `0.05` | Score weight per boost level |

## Tests

```bash
cd src/backend
python -m pytest tests/unit/test_editorial.py -v
```

23 unit tests covering:
- Domain helper (`_extract_domain`)
- Duplicate detection logic
- Manual submission stub creation
- Repository boost/suppress/unsuppress + idempotency
- Audit log writes
- Ranking integration (score increase, clamping)
- Auth source inspection (constant-time compare, fail-closed)
- Score explanation breakdown

## Design Decisions

1. **Server-rendered UI** — No React/JS build step. Pico CSS from CDN. Keeps the admin portal self-contained in the backend repo.
2. **Additive boost** — Editorial boost is additive, not multiplicative, to prevent overriding organic relevance.
3. **No hard deletes** — Suppress sets `is_suppressed=True`. Content remains in DB for audit purposes.
4. **Audit everything** — Every editorial action (add, boost, suppress, unsuppress) is logged to `editorial_actions` with old/new values and actor.
5. **Reused existing auth** — The `require_admin_key` dependency was already production-grade (constant-time compare, fail-closed).
