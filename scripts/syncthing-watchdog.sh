#!/usr/bin/env bash
set -euo pipefail

D="${HERMES_DATA:-/opt/data}"
ST_BIN="$D/.local/bin/syncthing"
ST_HOME="$D/.config/syncthing"
LOG_DIR="$D/logs"
LOG_FILE="$LOG_DIR/syncthing-watchdog.log"
mkdir -p "$LOG_DIR"

if pgrep -f "syncthing --no-browser --home=$ST_HOME" >/dev/null 2>&1 || pgrep -f "syncthing.*$ST_HOME" >/dev/null 2>&1; then
  exit 0
fi

if [ ! -x "$ST_BIN" ] || [ ! -f "$ST_HOME/config.xml" ]; then
  echo "syncthing watchdog: binary or config missing" >&2
  exit 1
fi

nohup "$ST_BIN" --no-browser --home="$ST_HOME" >> "$LOG_FILE" 2>&1 &
echo "syncthing watchdog: restarted at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
