---
name: article-image-edgecase-sweeper
description: Analyze article images from the last 24 hours, detect new extraction edge cases, patch the image extraction engine, add regression tests, and backfill affected content in blips-ai-news-backend. Use this for recurring article image quality sweeps, wrong-image investigations, low-resolution hero fixes, anti-bot classification review, and targeted repair/backfill work.
---

# Article Image Edge-Case Sweeper

Use this skill from `/Users/ra/dev/projects/blips/blips-ai-news-backend` when the task is to find newly introduced article-image failures and close the loop end to end.

## Required environment

- `BLIPS_BASE_URL`
  Default this to `https://blips-api.onrender.com`.
- `BLIPS_ADMIN_KEY`
  Required for editorial inspection and repair/backfill endpoints.

## Default workflow

1. Start with the recent audit script:
   `python3 .skills/article-image-edgecase-sweeper/scripts/audit_recent_article_images.py --hours 24`
2. Group the suspects by repeatable pattern before touching code:
   wrong body image, author/avatar image, related-card image, generic social/share art, suspicious proxy/tracker URL, missing image despite recoverable page metadata, or `BOT_PROTECTED`.
3. Inspect at least one representative item per pattern with:
   `python3 .skills/backend-inspector/scripts/inspect_content.py <content_id>`
4. Patch the real engine, usually in one or more of:
   `src/backend/app/extraction/metadata.py`
   `src/backend/app/article_hydration.py`
   `src/backend/app/extraction/normalize.py`
   `src/backend/app/article_image_selection.py`
5. Add regression coverage for every pattern you fix.
6. Run targeted local validation from `src/backend`:
   `ruff lint`
   `ruff format --check`
   `pytest` for the touched image-extraction scope
7. Backfill only the affected rows:
   targeted: `POST /api/v1/admin/trigger-article-image-repair`
   broad recent repair: `POST /api/v1/admin/trigger-image-repair`
   feed refresh after repair: `POST /api/v1/admin/trigger-content-touch`

## Guardrails

- Do not jump straight to LLM blame. First compare the stored image against a fresh extraction pass and the page’s own metadata/body candidates.
- Treat `BOT_PROTECTED` or fetch failures as their own class of issue. Do not mislabel them as extraction misses.
- Prefer targeted backfills using the content IDs surfaced by the audit. Only run a broad repair when the pattern is clearly widespread or the user asks for it.
- Do not silently backfill after code changes. Report which IDs or cohorts you are about to repair.
- Always finish with a compact summary:
  edge cases found, code changed, tests run, and the exact backfill actions taken.
