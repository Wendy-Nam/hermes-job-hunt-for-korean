# Proactive Opener Session Note

This note condenses the live lesson from the 선톡 (proactive opener) cron job run.

## What mattered in practice
- The live script is the source of truth when docs and prompt text drift.
- Silent skip means success when the script prints nothing.
- For randomized windows, persist the decision state instead of reseeding every tick.

## Observed pattern
- Live job: `선톡`
- Script path: `sunteok-check.sh`
- Script output empty at the moment of test → the real cron would not send a message.
- The script stores state in a JSON file with:
  - `last_contact_ts`
  - `target_ts`
  - `notified_for_ts`

## Practical rule
When asked “what would it send now?”, do not draft from expectation.
First run the gate; only generate content if the gate emits trigger context.
