# Phase 7: First Dev Deployment

## Status: IN PROGRESS

### Step 1: Push to Render ✅
```bash
# develop branch created and pushed
git push -u origin develop
```

### Step 2: Connect Render Blueprint

1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **New** → **Blueprint**
3. Connect your GitHub repo: `asarshad/blips-ai-news-backend`
4. Select the `develop` branch
5. Render will read `render.yaml` and create:
   - `blips-db-dev` (PostgreSQL)
   - `blips-redis-dev` (Redis)
   - `blips-api-dev` (Web service)
   - `blips-worker-dev` (Worker)

### Step 3: Set Secret Environment Variables

After blueprint deploys, set these secrets manually in Render dashboard:

**For blips-api-dev:**
```
OPENAI_API_KEY=sk-...your-key...
MISTRAL_API_KEY=...your-key... (optional)
```

**For blips-worker-dev:**
```
OPENAI_API_KEY=sk-...your-key...
MISTRAL_API_KEY=...your-key... (optional)
```

### Step 4: Verify Deployment

Once deployed, run these verification commands:

```bash
# Get your API URL from Render (e.g., https://blips-api-dev.onrender.com)
export API_URL="https://blips-api-dev.onrender.com"

# 1. Health check
curl $API_URL/health
# Expected: {"status":"healthy"}

# 2. Check feature flags
curl $API_URL/api/v1/admin/flags
# Expected: JSON with all feature statuses

# 3. Enable ingestion (if needed)
curl -X PUT $API_URL/api/v1/admin/flags/ingestion \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# 4. Trigger manual ingestion
curl -X POST "$API_URL/api/v1/admin/trigger/ingestion?limit=10"
# Expected: {"articles_found": N, "articles_processed": M, ...}

# 5. Check content stats
curl $API_URL/api/v1/admin/stats/content
# Expected: {"total_by_type": {...}, "total": N, "duplicate_urls": 0, ...}

# 6. Get articles
curl "$API_URL/api/v1/articles/recent?limit=5"
# Expected: List of articles
```

### Step 5: Verification Checklist

- [ ] `/health` returns 200
- [ ] `content_items` populated (check `/admin/stats/content`)
- [ ] No duplicate URLs (check `duplicate_urls` in stats)
- [ ] LLM usage within expected range (check OpenAI dashboard)

---

## Troubleshooting

### If deployment fails:
1. Check Render build logs for errors
2. Verify `requirements.txt` is complete
3. Check migration errors in logs

### If ingestion fails:
1. Check feature flags: `curl $API_URL/api/v1/admin/flags`
2. Enable ingestion if disabled
3. Check worker logs in Render dashboard

### If migrations fail:
```bash
# Run migrations manually via Render Shell
cd src/backend
alembic upgrade head
```

### Common Issues:
- **Missing OPENAI_API_KEY**: Set in Render dashboard
- **Redis connection failed**: Check Redis service is running
- **Database connection failed**: Check DATABASE_URL in environment
