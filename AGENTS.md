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
