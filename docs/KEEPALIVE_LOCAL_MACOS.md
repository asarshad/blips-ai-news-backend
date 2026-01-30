# Local keep-alive (macOS)

GitHub Actions scheduled workflows can be delayed (quota / best-effort scheduling). If you want more reliable keep-alive behavior, you can run the keep-alive pinger locally on your Mac as a background service using `launchd`.

This uses the existing script: `.github/scripts/keep_alive_ping.py`

## Install

From the repo root:

```bash
cd /Users/aarshad/dev/projects/blips/blips-ai-news-backend

export HEALTH_URL="https://YOUR-SERVICE.onrender.com/health"

# Optional: reduce spam to ~1 ping per run
export INTERVAL_SECONDS=60
export DURATION_SECONDS=60
export TIMEOUT_SECONDS=10

bash tools/keepalive/macos/install_keepalive_macos.sh
```

The service will:
- run immediately on install
- then run every 5 minutes (`StartInterval=300`)

Logs:
- `~/Library/Logs/blips-keepalive.log`
- `~/Library/Logs/blips-keepalive.err.log`

## Uninstall

```bash
bash tools/keepalive/macos/uninstall_keepalive_macos.sh
```

## Notes

- `/health` should stay lightweight because it may be called frequently.
- The pinger script never logs URL query params (to avoid leaking secrets).
- If you use a Python virtualenv in this repo (`.venv`), the install script will prefer it.
