---
name: backend-inspector
description: Inspect the Blips backend through the deployed public and admin APIs, bootstrap anonymous sessions, probe article payloads, and compare stored content against fresh source-page metadata. Use this for production debugging, endpoint checks, article/image triage, and admin-trigger verification in blips-ai-news-backend.
---

# Backend Inspector

Use this skill from `/Users/ra/dev/projects/blips/blips-ai-news-backend` when the task is to inspect backend behavior rather than immediately patch code.

## What this skill is for

- Verify deployed behavior on Render after a push.
- Inspect one content item end to end through admin detail, public payload, and source-page metadata.
- Exercise admin trigger endpoints safely and record the exact response.
- Reproduce image incidents before changing extraction logic.

## Required environment

- `BLIPS_BASE_URL`
  Default this to `https://blips-api.onrender.com` unless the user points at another deploy.
- `BLIPS_ADMIN_KEY`
  Required for admin/editorial and trigger endpoints.

## Default workflow

1. Run the existing operational probe first:
   `cd src/backend && python3 scripts/operational_check.py --base-url "$BLIPS_BASE_URL"`
2. For a specific article/content incident, run:
   `python3 .skills/backend-inspector/scripts/inspect_content.py 294886`
3. If you need raw endpoint confirmation, use these patterns:
   `curl -s "$BLIPS_BASE_URL/api/v1/admin/editorial/content?type=ARTICLE&page=1&page_size=50" -H "X-Admin-Key: $BLIPS_ADMIN_KEY"`
   `curl -s "$BLIPS_BASE_URL/api/v1/admin/editorial/content/294886" -H "X-Admin-Key: $BLIPS_ADMIN_KEY"`
4. For public article payloads, either let `inspect_content.py` bootstrap a bearer token or do it manually:
   `curl -s -X POST "$BLIPS_BASE_URL/api/v1/auth/session" -H "content-type: application/json" -d '{"platform":"ios","app_version":"codex-inspector"}'`

## Admin triggers to know

- Image repair backfill:
  `POST /api/v1/admin/trigger-image-repair`
- Single article image repair:
  `POST /api/v1/admin/trigger-article-image-repair`
- Image recovery evaluation:
  `POST /api/v1/admin/trigger-image-recovery-eval`
- Feed/cache bump after repairs:
  `POST /api/v1/admin/trigger-content-touch`

## Guardrails

- Treat production inspection as read-mostly. Do not call write/admin trigger endpoints unless the user asked for an action or the task clearly requires it.
- When an image looks wrong, inspect three things before proposing code changes:
  the admin record, the public article payload, and the fresh source-page metadata.
- If the page fetch is blocked and the fetcher reports `BOT_PROTECTED`, record that separately from true extraction misses.
- Prefer the bundled script for content triage so the output stays consistent across agents.
