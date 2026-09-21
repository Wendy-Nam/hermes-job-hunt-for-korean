# Random Window Proactive Jobs

This note captures the pattern used for a proactive Discord message job that should fire only after a randomized delay.

## Goal

Send a message **30–60 minutes after the last user/assistant contact**, not on a fixed cron boundary.

## Pattern

Use a fast cron tick plus persistent state:

1. Cron runs frequently (e.g. every 5 minutes).
2. Script reads the latest user/assistant timestamp from `state.db`.
3. Script stores a per-conversation state object with:
   - `last_contact_ts`
   - `target_ts` = `last_contact_ts + random(30m..60m)`
   - `notified_for_ts`
4. If `now < target_ts`, script exits 0 with empty stdout.
5. If `now >= target_ts` and `notified_for_ts != last_contact_ts`, script prints structured context for the LLM and marks that contact as notified.
6. If a newer contact arrives, the script generates a new random target.

## Practical notes

- Keep the state file under `$HERMES_HOME/state/` or another durable, job-local location.
- Use `SystemRandom()` if you want non-deterministic selection.
- Keep the active-hours policy in sync across script, prompt, and docs.
- For silent jobs, empty stdout must mean "do nothing".
- For user-visible delivery, prefer a direct gateway target like Discord/Telegram.

## Minimal state shape

```json
{
  "last_contact_ts": 1783292619,
  "target_ts": 1783295222,
  "notified_for_ts": null
}
```

## Common bug

Do not trigger on the cron schedule itself. Trigger on the randomized target timestamp, otherwise the job becomes a fixed-interval spammer with a random delay only on the first run.
