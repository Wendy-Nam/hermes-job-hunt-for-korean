# Syncthing background operation notes

Use this when a Syncthing-backed Obsidian/wiki vault stops syncing even though the folder paths look correct.

## Server-side diagnosis

Check whether Syncthing is actually running before blaming the folder:

```bash
pgrep -af syncthing || true
ss -ltnp 2>/dev/null | grep -E '8384|22000|21027' || true
stat -c '%U %G %A %n' /opt/data/wiki /opt/data/wiki/.stfolder 2>/dev/null || true
```

A common failure mode on lightweight/containerized Hermes hosts is that `systemctl` is unavailable and Syncthing was only started in the foreground. When that terminal/process dies, sync silently stops.

## Server foreground test

```bash
/opt/data/.local/bin/syncthing --no-browser --home=/opt/data/.config/syncthing
```

Look for:

```text
Ready to synchronize (folder.label="Wiki (일상관리)" folder.id=wiki ...)
```

## Server watchdog pattern

Create a token-free watchdog script under `$HERMES_HOME/scripts/` and schedule it with `no_agent=True`.

Example script path:

```text
/opt/data/scripts/syncthing-watchdog.sh
```

Core logic:

```bash
#!/usr/bin/env bash
set -euo pipefail
ST_BIN="/opt/data/.local/bin/syncthing"
ST_HOME="/opt/data/.config/syncthing"
LOG_DIR="/opt/data/logs"
LOG_FILE="$LOG_DIR/syncthing-watchdog.log"
mkdir -p "$LOG_DIR"

if pgrep -f "syncthing.*$ST_HOME" >/dev/null 2>&1; then
  exit 0
fi

nohup "$ST_BIN" --no-browser --home="$ST_HOME" >> "$LOG_FILE" 2>&1 &
echo "syncthing watchdog: restarted at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
```

Schedule:

```text
cronjob(action="create", name="syncthing-watchdog", schedule="every 5m", script="syncthing-watchdog.sh", no_agent=True, deliver="local")
```

## REST verification

Use the API key from `/opt/data/.config/syncthing/config.xml` and query:

```text
/rest/system/connections
/rest/db/status?folder=wiki
/rest/db/completion?folder=wiki&device=<MAC_DEVICE_ID>
```

Healthy signs:

```text
folder state: idle
errors: 0
pullErrors: 0
needFiles: 0
completion: 100
remoteState: valid
```

## macOS background operation

On the Mac, do not rely on a foreground terminal process. Use Homebrew services:

```bash
brew install syncthing
brew services start syncthing
brew services list | grep syncthing
```

Manage it with:

```bash
brew services restart syncthing
brew services stop syncthing
brew services info syncthing
```

GUI:

```text
http://127.0.0.1:8384
```

Path sanity check:

```text
Mac:    /Users/<user>/wiki
Server: /opt/data/wiki
```

If server status is healthy but Obsidian does not show files, suspect the Mac Syncthing folder path or Obsidian vault selection before changing the server.
