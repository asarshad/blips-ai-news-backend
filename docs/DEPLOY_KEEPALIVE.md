# Keep-alive for free Render instances

Free-tier Render services can spin down due to inactivity. This repo includes an optional GitHub Actions workflow that periodically pings the service to keep it warm.

## GitHub Action

Workflow: `.github/workflows/keep-alive.yml`

- Schedule: every 5 minutes
- Endpoint: `/health`

### Setup

1. In GitHub repo settings → **Secrets and variables** → **Actions**, add:
   - `KEEPALIVE_URL` = `https://YOUR-SERVICE.onrender.com` (or the full `/health` URL)

2. Confirm the workflow is enabled.

### Lower frequency

If you prefer lower frequency, change the cron to every 10 minutes:

```yaml
- cron: "*/10 * * * *"
```

## Alternatives

- External uptime monitors (UptimeRobot, Better Stack, etc.)
- A lightweight cron job from your own infrastructure

## Notes

- GitHub schedules are best-effort and can drift.
- This does not replace proper worker architecture for background jobs.
