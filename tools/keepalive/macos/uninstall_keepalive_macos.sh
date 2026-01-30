#!/usr/bin/env bash
set -euo pipefail

LABEL="com.blips.keepalive"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$PLIST_PATH" ]]; then
  launchctl bootout gui/"$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "Removed launchd agent: $LABEL"
else
  echo "No plist found at $PLIST_PATH (already uninstalled?)"
fi

echo "Tip: logs are in ~/Library/Logs/blips-keepalive.log and blips-keepalive.err.log"
