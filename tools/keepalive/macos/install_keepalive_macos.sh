#!/usr/bin/env bash
set -euo pipefail

# Installs a per-user launchd agent that pings HEALTH_URL every 5 minutes.
#
# Usage:
#   export HEALTH_URL="https://YOUR-SERVICE.onrender.com/health"
#   ./tools/keepalive/macos/install_keepalive_macos.sh
#
# Optional overrides:
#   INTERVAL_SECONDS (default: 60)  # interval between pings within a single run
#   DURATION_SECONDS (default: 60)  # total time spent pinging per run
#   TIMEOUT_SECONDS  (default: 10)
#
# Notes:
# - This runs once every 300s (5 minutes) via launchd.
# - Default config sends ~1 ping per run to avoid spam.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PINGER_PY="$ROOT_DIR/.github/scripts/keep_alive_ping.py"

if [[ ! -f "$PINGER_PY" ]]; then
  echo "ERROR: keep-alive script not found at $PINGER_PY" >&2
  exit 1
fi

HEALTH_URL="${HEALTH_URL:-}"
if [[ -z "$HEALTH_URL" ]]; then
  echo "ERROR: HEALTH_URL is required." >&2
  echo "Example: export HEALTH_URL=\"https://YOUR-SERVICE.onrender.com/health\"" >&2
  exit 2
fi

PYTHON_BIN=""
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "ERROR: Could not find python (expected $ROOT_DIR/.venv/bin/python or python3 on PATH)" >&2
  exit 3
fi

LABEL="com.blips.keepalive"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
OUT_LOG="$LOG_DIR/blips-keepalive.log"
ERR_LOG="$LOG_DIR/blips-keepalive.err.log"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"
DURATION_SECONDS="${DURATION_SECONDS:-60}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-10}"

cat >"$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
      <string>$PYTHON_BIN</string>
      <string>$PINGER_PY</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$ROOT_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
      <key>HEALTH_URL</key>
      <string>$HEALTH_URL</string>
      <key>INTERVAL_SECONDS</key>
      <string>$INTERVAL_SECONDS</string>
      <key>DURATION_SECONDS</key>
      <string>$DURATION_SECONDS</string>
      <key>TIMEOUT_SECONDS</key>
      <string>$TIMEOUT_SECONDS</string>
      <key>FAIL_ON_ERROR</key>
      <string>false</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>StartInterval</key>
    <integer>300</integer>

    <key>StandardOutPath</key>
    <string>$OUT_LOG</string>

    <key>StandardErrorPath</key>
    <string>$ERR_LOG</string>
  </dict>
</plist>
PLIST

# (Re)load agent
launchctl bootout gui/"$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap gui/"$(id -u)" "$PLIST_PATH"
launchctl enable gui/"$(id -u)"/$LABEL
launchctl kickstart -k gui/"$(id -u)"/$LABEL

echo "Installed launchd agent: $LABEL"
echo "Plist: $PLIST_PATH"
echo "Logs: $OUT_LOG (stderr: $ERR_LOG)"
echo "To stop/remove: ./tools/keepalive/macos/uninstall_keepalive_macos.sh"
