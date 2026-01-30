# Keep-alive for free Render instances

Free-tier Render services can spin down due to inactivity.

GitHub Actions scheduled workflows are best-effort and can be delayed (often by a lot) due to quota / scheduling. For more reliable keep-alive behavior, this repo now recommends running keep-alive locally.

## Recommended: local keep-alive (macOS)

See [docs/KEEPALIVE_LOCAL_MACOS.md](docs/KEEPALIVE_LOCAL_MACOS.md).

## GitHub Actions (disabled)

The workflow file still exists at `.github/workflows/keep-alive.yml`, but the cron schedule has been removed. You can run it manually via `workflow_dispatch`, but it is no longer relied upon for keeping Render warm.

## Configuration

The pinger script reads these env vars (with safe defaults):

- `HEALTH_URL` (required to actually ping)
- `INTERVAL_SECONDS` (default: `45`)
- `DURATION_SECONDS` (default: `360`)
- `TIMEOUT_SECONDS` (default: `10`)
- `FAIL_ON_ERROR` (default: `false`) — by default the process exits 0 even if pings fail

If you manually run the (disabled) GitHub workflow, it supports repo variables named `KEEPALIVE_INTERVAL_SECONDS`, `KEEPALIVE_DURATION_SECONDS`, `KEEPALIVE_TIMEOUT_SECONDS`, `KEEPALIVE_FAIL_ON_ERROR` and maps them to the script env vars above.

Guardrails:

- Interval is clamped to $[20, 120]$ seconds.
- Duration is clamped to $[60, 600]$ seconds.
- Timeout is clamped to $[2, 30]$ seconds.

## Disable

- For local macOS keep-alive: run `bash tools/keepalive/macos/uninstall_keepalive_macos.sh`.
- For manual script runs: unset `HEALTH_URL` (the script will log a warning and exit 0).
- For GitHub Actions: it is already not scheduled (manual only).

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
