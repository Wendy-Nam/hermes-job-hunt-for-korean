# Daily Logging Pattern — Full Example

> Canonical implementation used for daily wiki change logging (2026-07-05).
> Deployed at `/opt/data/scripts/daily-wiki-log.py`, cron job `daily-wiki-log`.

## Script: `daily-wiki-log.py`

```bash
#!/bin/bash
# daily-wiki-log.py
# Check for wiki changes today and append to log.md.
# If no changes, exit silently (empty stdout → no delivery noise).

WIKI_DIR="/opt/data/wiki"
LOG_FILE="$WIKI_DIR/log.md"
TODAY=$(TZ=Asia/Seoul date +%Y-%m-%d)

cd "$WIKI_DIR" || exit 0

# Find .md files modified today (exclude log.md itself)
CHANGED=$(find . -name "*.md" -not -name "log.md" \
  -newermt "$TODAY 00:00:00" -not -newermt "$TODAY 23:59:59" 2>/dev/null | sort)

if [ -z "$CHANGED" ]; then
  # No changes today — silent
  exit 0
fi

# Build log entry
ENTRY="## [$TODAY] daily-log | Wiki changes detected"
while IFS= read -r f; do
  f="${f#./}"
  SIZE=$(stat -c%s "$f" 2>/dev/null || echo "?")
  ENTRY="$ENTRY
- $f ($SIZE bytes)"
done <<< "$CHANGED"

# Append to log.md
echo "$ENTRY" >> "$LOG_FILE"
echo "$ENTRY"
```

## Cron config

```
job_id: daily-wiki-log
schedule: 59 14 * * *      # 23:59 KST
deliver: discord            # notify user when changes detected
no_agent: true              # zero-token script-only execution
script: daily-wiki-log.py
```

## Behavior

| Scenario | Script exit | stdout | Delivery | Token cost |
|----------|------------|--------|----------|------------|
| No wiki changes today | exit 0 | empty | **silent** (nothing delivered) | 0 |
| Wiki files modified | exit 0 | log entry | Discord message sent | 0 |

## Design decisions

- **`no_agent=true`**: The script IS the job. No LLM involvement at all — zero tokens burned. stdout is delivered verbatim.
- **Empty stdout = silent**: `no_agent` jobs with empty stdout do NOT deliver anything. This is the key to noise-free daily logging — most days nothing changes, and those days cost nothing and produce no notification.
- **`deliver=discord`**: On days when changes ARE detected, the log entry is delivered to Discord so the user sees it. The same entry is also appended to log.md on disk.
- **`find -newermt`**: Uses file modification time, not git. Works even if the wiki directory is not a git repo. Timezone-aware via `TZ=Asia/Seoul`.
- **Self-exclusion**: `-not -name "log.md"` prevents the log file from triggering itself when it gets appended.