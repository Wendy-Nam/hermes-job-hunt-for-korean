---
name: efficient-cron-jobs
description: "Hermes-only. Use when creating cron jobs that need token-efficient background execution — script-gate patterns, condition pre-check, DB-queried state, and minimal-cost silent skips."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, scheduling, optimization, tokens, background, automation]
    related_skills: [hermes-agent, cron-job-orchestration]
---

# Efficient Cron Jobs

## Overview

Cron jobs that run every 15-30 minutes can waste significant tokens if the agent runs the full LLM pipeline on every tick even when there's nothing to do. This skill captures patterns for **token-optimized background agent tasks** — where a lightweight script pre-checks conditions, and the LLM only fires when actual work is needed.

## Core Pattern: Script Gate

The most effective pattern is a two-tier architecture:

```
Tick fires
  → Script runs (bash/Python, ~0 tokens)
    → Conditions NOT met → stdout empty → SILENT (agent gets empty context → exits instantly)
    → Conditions met → stdout has context data → agent generates rich response
```

### Script Placement

Scripts live under `~/.hermes/scripts/<name>.sh` (or `.py`). Relative path only:
```bash
# Good — cron job script field:
script: sunteok-check.sh

# Bad — absolute paths rejected by cronjob tool:
script: /home/user/scripts/sunteok-check.sh
```

### What the script checks

Typical cheap pre-checks (each costs ~0 tokens):

1. **Time window** — active hours only (e.g. 07:00~익일 02:00 KST)
   ```bash
   HOUR=$(TZ=Asia/Seoul date +%H)
   if [ "$HOUR" -ge 2 ] && [ "$HOUR" -lt 7 ]; then exit 0; fi
   ```

2. **Last contact time** — query `state.db` SQLite directly
   ```python
   import sqlite3, time
   conn = sqlite3.connect('/opt/data/state.db')
   cur = conn.execute('SELECT MAX(timestamp) FROM messages WHERE role IN ("user","assistant")')
   last_ts = cur.fetchone()[0]
   conn.close()
   elapsed = time.time() - last_ts
   if elapsed < 1800: print()  # silent — < 30 min
   ```

3. **File existence / content change**
   ```bash
   if [ ! -f /tmp/trigger-file ]; then exit 0; fi
   ```

4. **Day of week / date check**
   ```bash
   DOW=$(TZ=Asia/Seoul date +%u)  # 1=Mon..7=Sun
   if [ "$DOW" -ge 6 ]; then exit 0; fi  # skip weekends
   ```

### Script output conventions

| Condition | stdout | Agent behavior | Token cost |
|-----------|--------|----------------|------------|
| Not met | Empty (exit 0) | Agent context empty → exits without action | ~base tokens |
| Met | Structured context data | Agent enriches with calendar/wiki/search → generates message | Full cost |

Structured context format:
```
=== TRIGGER ===
key: value
key2: value2
```

## State.db Schema Reference

The Hermes session store (`state.db`) has these useful tables:

### messages table
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| session_id | TEXT FK→sessions | Which session |
| role | TEXT | user, assistant, tool |
| content | TEXT | Message content |
| timestamp | REAL | Unix timestamp (UTC) |
| finish_reason | TEXT | stop, tool_calls, etc. |

### sessions table
| Column | Type | Purpose |
|--------|------|---------|
| id | TEXT PK | Session UUID |
| source | TEXT | cli, discord, telegram, cron |
| started_at | REAL | Session start (UTC) |
| ended_at | REAL | Session end (UTC) |
| title | TEXT | Session title |
| message_count | INTEGER | Messages in session |

### Useful queries

```python
# Last message timestamp (any role)
SELECT MAX(timestamp) FROM messages

# Last user message
SELECT MAX(timestamp) FROM messages WHERE role='user'

# Recent session titles
SELECT title, started_at FROM sessions ORDER BY started_at DESC LIMIT 5

# Count messages in last N hours
SELECT COUNT(*) FROM messages WHERE timestamp > (strftime('%s','now') - N*3600)
```

## Two-Tier Cron Job Pattern

When you need truly zero-token silent skips, use TWO linked cron jobs:

```
Job A: check conditions (no_agent=True, script only)
  → stdout empty = silent (0 tokens)
  → stdout has data = stored as last output
  
Job B: generate response (context_from=[A])
  → Injected with A's last output as context
  → If context empty → exits immediately
  → If context has trigger → generates rich response
```

Important: Job B's output MUST be empty when context is empty. If the agent evaluates empty context and _decides_ to do nothing, it still consumed base tokens for that evaluation. The two-tier pattern prevents this by using `no_agent=True` on Job A — the LLM never runs.

## Randomized Proactive Window Pattern

Use this when the user wants a message to happen **sometime after a floor, but not on a fixed cron boundary** — for example, "send 30–60 minutes after the last contact."

Pattern:

1. Run a frequent silent cron tick (e.g. every 5 minutes).
2. Script reads the latest user/assistant contact timestamp from `state.db`.
3. Script chooses one randomized `target_ts` in the desired window and persists it in a small state file.
4. Empty stdout means "not yet"; structured stdout means "fire now."
5. Record `notified_for_ts` so the same contact only fires once.
6. If a newer contact arrives, clear/reseed the target.
7. Re-seed only when the latest contact timestamp changes; do **not** draw a new random target on every tick.

## Live Proactive-Opener Verification

When the user asks what a proactive opener would send *now*, do not infer from the prompt alone.

1. Inspect the live gate or script path.
2. Execute the script under the current timezone.
3. If stdout is empty, the real job stays silent.
4. If the script emits trigger context, use that output as the only source for the draft.
5. Treat the live gate as the source of truth when docs and prompt text drift.

Reference: `references/random-window-proactive.md`.

## Delivery Target Truth Source

Cron delivery is resolved from the job record, not from the English text in the prompt preview.

- If the user wants the current Hermes thread, prefer `attach_to_session=true` and verify the resulting target instead of trusting the prompt text.
- If the user wants Discord/Telegram/other chat delivery, verify the real gateway target exists before assuming it will land in a DM or thread.
- When Hermes must read Discord chat text as context, the Discord bot also needs **Message Content Intent** enabled in the developer portal.
- When you update `deliver`, also patch the prompt text if it still says the old channel name; stale prompt text is harmless to execution but confusing during debugging.
- If a run says `no delivery target resolved`, trust that log. Do not treat the job name or prompt wording as proof that delivery is wired.
- If the scheduler skips a job because the global provider/model drifted since creation, pin the job explicitly with the current provider/model before expecting any delivery.
- `cronjob list` reports `next_run_at` in UTC; convert to KST before answering the user when they ask for "다음 실행" or similar timing.

See `references/discord-delivery-pitfalls.md` for concrete delivery/debugging examples.

## Toolset Optimization

```python
# Minimal — time check + file read only
enabled_toolsets=["terminal", "file"]

# With search capability
enabled_toolsets=["terminal", "file", "session_search"]

# Full context collection
enabled_toolsets=["web", "terminal", "file", "delegation", "session_search", "memory"]
```

Every unnecessary toolset adds tokens to every system prompt. Strip aggressively.

## Common Pitfalls

1. **Script stays in wrong directory** — Scripts must be in `~/.hermes/scripts/` (which is `$HERMES_HOME/scripts/`). Cron job accepts only relative script paths resolving under that directory.
2. **Script not executable** — `chmod +x ~/.hermes/scripts/<name>.sh`
3. **Script references wrong db path** — `state.db` lives at `$HERMES_HOME/state.db` (e.g. `/opt/data/state.db`), not `~/.hermes/state.db` when `HERMES_HOME` is set.
4. **KST/UTC confusion** — `messages.timestamp` is stored in UTC. Always add KST offset (+32400s = 9h) when displaying to user. For schedules, `0 23 * * *` UTC equals 08:00 KST.
5. **Delivery target must match the chat surface** — Verify the real delivery target with `cronjob list` after changing it. `deliver='origin'` is appropriate when you want the job to return in the current Hermes chat thread; use `discord`/`telegram`/etc. only when that gateway is actually wired. Do not assume the target from the job name or prompt text.
6. **30-min check on first run** — First cron run may trigger immediately if DB has old sessions. Add a minimum threshold check.
7. **no_agent=True jobs cannot collect context** — They run purely as scripts and deliver stdout verbatim. No LLM, no tool access. Use for pure watchdog/heartbeat patterns only.
8. **Empty stdin from script with conditions not met** — In default mode (no_agent=False), the agent STILL runs and gets the empty context. It will consume base tokens deciding "nothing to do." For truly zero-cost ticks, use the two-tier pattern or a very short prompt that exits on empty context.
9. **Do not improvise proactive-message content** — If the user asks what a 선톡/proactive cron would send “now,” first evaluate the actual gate and context: inspect the cron job and script, run the script with current KST, check latest cron output if relevant, and only then answer. If the script stdout is empty, the correct answer is “the real job would send nothing now,” not a plausible invented message.
10. **Prompt/script window drift** — Keep the active-hour policy consistent across the script, cron prompt, and verification. For the live 선톡 job, the source of truth is the script window: 07:00~익일 02:00 KST. If any prompt or docs drift from that, patch them immediately instead of hand-waving it.
11. **Empty assistant rows can reset silence forever** — When querying `state.db` for “last contact,” filter out empty content rows: `AND COALESCE(TRIM(content), "") != ""`. Tool-call bookkeeping and silent cron runs can create empty assistant messages; counting them makes the random window reseed every tick and the proactive message never fires.
12. **Randomized proactive window** — If the user wants “30 minutes after last contact, but not on a fixed boundary,” do not encode the randomness in cron itself. Run a short silent tick, persist a `target_ts` in a small state file, and reseed only when the last-contact timestamp changes. See `references/random-window-proactive.md` once it exists.

## Verification Checklist

- [ ] Script is in `$HERMES_HOME/scripts/` and executable
- [ ] Script produces empty stdout when conditions not met
- [ ] Script produces structured context when conditions met
- [ ] Cron job `enabled_toolsets` stripped to minimum needed
- [ ] UTC/KST timezone handled correctly
- [ ] State.db queries use correct path and column names
- [ ] Delivery target (discord/telegram/etc) matches user's gateway setup

## Real-World Example

See `references/sunteok-gate-example.md` for a complete working implementation — the 선톡 (proactive Discord message) cron job using script-gate + state.db queries + KST timezone handling + Discord delivery.

For recurring Korean job briefings, see `references/job-search-briefing.md` for the current search families, 3-year hard cutoff, and Discord link-preview hygiene.

## Daily Logging Pattern (no_agent script for file append)

Use a `no_agent=True` cron job with a script that detects filesystem changes and appends to a log — zero tokens, pure bash. Ideal for wiki change tracking, system event logging, or audit trails.

Pattern:
```bash
#!/bin/bash
TODAY=$(TZ=Asia/Seoul date +%Y-%m-%d)
CHANGED=$(find /path/to/watch -name "*.md" -not -name "log.md" \
  -newermt "$TODAY 00:00:00" -not -newermt "$TODAY 23:59:59" 2>/dev/null)

if [ -z "$CHANGED" ]; then exit 0; fi

ENTRY="## [$TODAY] daily-log | changes detected"
while IFS= read -r f; do
  ENTRY="$ENTRY\n- $f"
done <<< "$CHANGED"
echo -e "$ENTRY" >> /path/to/log.md
echo -e "$ENTRY"  # stdout → delivered
```

Cron job config: `no_agent: true`, `schedule: 59 14 * * *` (23:59 KST), `deliver: discord`.

Pitfalls:
- **Log file inside watched directory** — exclude the log file itself from `find` (`-not -name "log.md"`) or you'll get infinite self-triggering.
- **Timezone** — crontab runs in server timezone (UTC). Use `TZ=Asia/Seoul` inside the script for date calculations.
- **Race conditions** — for daily jobs at off-peak hours, interleaved appends are acceptable. For higher-frequency logging use a lock file.

Full annotated example: `references/daily-logging-pattern.md`.