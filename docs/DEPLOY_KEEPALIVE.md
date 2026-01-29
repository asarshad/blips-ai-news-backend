# Keep-alive for free Render instances

Free-tier Render services can spin down due to inactivity. This repo includes an optional GitHub Actions workflow that periodically pings the service to keep it warm.

## GitHub Action

Workflow: `.github/workflows/keep-alive.yml`

- Schedule: every 5 minutes
- Inner loop: within each run, pings about every ~45 seconds for several minutes (7–8 calls)
- Endpoint: `/health` (intentionally lightweight)

### Setup

1. In GitHub repo settings → **Secrets and variables** → **Actions**, add:
   - Recommended (repo variable): `HEALTH_URL` = `https://YOUR-SERVICE.onrender.com/health`
     - You can also set `HEALTH_URL` to the base URL (the script will still work if it includes `/health`).
   - Optional fallback (secret): `KEEPALIVE_URL` if you prefer storing the URL as a secret.

2. Confirm the workflow is enabled.

## Configuration

The workflow reads these variables (with safe defaults in the script):

- `HEALTH_URL` (required to actually ping)
- `KEEPALIVE_INTERVAL_SECONDS` (default: `45`)
- `KEEPALIVE_DURATION_SECONDS` (default: `360`)
- `KEEPALIVE_TIMEOUT_SECONDS` (default: `10`)
- `KEEPALIVE_FAIL_ON_ERROR` (default: `false`) — by default the job stays green even if pings fail

Guardrails:

- Interval is clamped to $[20, 120]$ seconds.
- Duration is clamped to $[60, 600]$ seconds.
- Timeout is clamped to $[2, 30]$ seconds.

## Disable

- Disable the workflow in GitHub Actions UI, or
- Remove the cron trigger in `.github/workflows/keep-alive.yml`, or
- Unset `HEALTH_URL` (the script will log a warning and exit 0).

## Local testing

```bash
export HEALTH_URL="https://YOUR-SERVICE.onrender.com/health"
python3 .github/scripts/keep_alive_ping.py
```

You can also override behavior:

```bash
export INTERVAL_SECONDS=45
export DURATION_SECONDS=360
export TIMEOUT_SECONDS=10
python3 .github/scripts/keep_alive_ping.py
```

### Lower frequency

If you prefer lower frequency, change the cron to every 10 minutes:

```yaml
- cron: "*/10 * * * *"
```

## Alternatives

- External uptime monitors (UptimeRobot, Better Stack, etc.)
- Cloudflare Cron Triggers / Workers
- A lightweight cron job from your own infrastructure
- Upgrade to a paid Render instance

## Notes

- GitHub schedules are best-effort and can drift.
- This should not be “spammy”: the defaults are ~8 requests per 5 minutes.
- The script avoids logging URL query parameters to reduce risk of leaking secrets.
- This does not replace proper worker architecture for background jobs.
