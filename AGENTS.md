# Agent Notes

## Push policy

- `blips-ai-news-backend` deploys to Render directly on push.
- Do not rely on GitHub checks to gate deployment.
- Before any push to `main`, run the relevant local CI for the touched backend scope and make sure it passes.

## Minimum local CI for `blips-ai-news-backend`

- From `/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend`
- `ruff lint`
- `ruff format --check`
- `pytest` for the impacted scope at minimum

## Operational expectation

- Treat every push to `main` as deployable.
- If Render is configured to wait on GitHub checks, switch it to deploy on commit and keep local validation as the gate.

## Repo-local skills

- Use [`./.skills/backend-inspector/SKILL.md`](/Users/ra/dev/projects/blips/blips-ai-news-backend/.skills/backend-inspector/SKILL.md) when you need to inspect production/backend behavior quickly through public APIs, admin endpoints, and fresh source-page metadata.
- Use [`./.skills/article-image-edgecase-sweeper/SKILL.md`](/Users/ra/dev/projects/blips/blips-ai-news-backend/.skills/article-image-edgecase-sweeper/SKILL.md) when you need to analyze recent article images, identify new extraction edge cases, patch the engine, and backfill affected rows.
- Both skills assume `BLIPS_BASE_URL` for the deployed API base URL and `BLIPS_ADMIN_KEY` for admin-only endpoints. Public article inspection can bootstrap its own anonymous bearer token through `/api/v1/auth/session`.
