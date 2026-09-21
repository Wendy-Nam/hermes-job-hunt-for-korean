# 선톡 Cron Gate — Real-World Example

## Job Configuration

```python
# 매 30분 체크, 07:00~익일 02:00 KST만 활성
# Script pre-checks conditions, triggers agent only when needed
cronjob(
    action='create',
    name='선톡',
    schedule='every 30m',
    script='sunteok-check.sh',     # ~/.hermes/scripts/sunteok-check.sh
    deliver='discord',
    enabled_toolsets=['terminal', 'file', 'session_search', 'delegation', 'memory'],
)
```

## Script: sunteok-check.sh

Location: `$HERMES_HOME/scripts/sunteok-check.sh`

```bash
#!/usr/bin/env bash
set -e

KST_HOUR=$(TZ=Asia/Seoul date +%H)
KST_DATE=$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S (%A)')

# 1. Time window check (07:00 ~ 익일 02:00 KST)
if [ "$KST_HOUR" -ge 2 ] && [ "$KST_HOUR" -lt 7 ]; then
    exit 0  # silent
fi

# 2. Query state.db for last message time
DB="/opt/data/state.db"
LAST_TS=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.execute('SELECT MAX(timestamp) FROM messages WHERE role IN (\"user\",\"assistant\")')
row = cur.fetchone()
conn.close()
print(int(row[0]) if row and row[0] else 0)
")

[ "$LAST_TS" = "0" ] && exit 0

# KST = UTC+9, messages.timestamp is UTC
NOW_UTC=$(date +%s)
ELAPSED=$((NOW_UTC - LAST_TS))

# 3. 30-min silence threshold
[ "$ELAPSED" -lt $((30 * 60)) ] && exit 0

# 4. Conditions met — output context
ELAPSED_MIN=$((ELAPSED / 60))
echo "=== 선톡 트리거 ==="
echo "current_kst: $KST_DATE"
last_kst=$(TZ=Asia/Seoul date -d "@$((LAST_TS + 32400))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
echo "last_contact_kst: $last_kst"
echo "elapsed_minutes: $ELAPSED_MIN"
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Bash (not Python) for main script | Simpler dependency, faster startup |
| Python inline for DB query | sqlite3 not available in pure bash |
| UTC→KST offset in script | messages.timestamp is UTC; +32400s = 9h |
| exit 0 (not exit 1) on skip | Non-zero exit triggers error delivery |
| Structured "=== TRIGGER ===" header | Easy for LLM prompt to detect vs ignore |

## Simulating “What would it send now?”

When the user asks what the 선톡 job would send right now, do **not** answer from vibes. Reconstruct the real cron path:

1. Load this reference and the active cron job configuration.
2. Inspect the live `sunteok-check.sh` script; the active-hour window in the live script is the source of truth if docs drift.
3. Run the script under current KST. Empty stdout means the real cron would send **nothing**.
4. Only if stdout contains `=== 선톡 트리거 ===`, gather the job prompt context: current KST, calendar, wiki/log, and recent session topics, then draft the message.
5. If docs, prompt, and script disagree about active hours, treat the live script window (07:00~익일 02:00 KST) as source of truth and patch the drift before drafting.

## Token Cost Analysis

| Scenario | Script cost | Agent cost | Total |
|----------|-------------|------------|-------|
| Nighttime skip (02-07) | ~0.001¢ (bash) | $0 | ~0.001¢ |
| Within 30 min of contact | ~0.001¢ (bash + sqlite3) | $0 | ~0.001¢ |
| Trigger fires | ~0.001¢ | ~$0.01-0.03 (LLM) | ~$0.01-0.03 |

Estimated daily: ~2-3 triggers × $0.02 + 45 skips × $0.00 = **~$0.05/day**