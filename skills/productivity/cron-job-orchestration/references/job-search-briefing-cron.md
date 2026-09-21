# Recurring Job-Search Briefing Cron

Use this pattern when the user wants a scheduled job that gathers and summarizes job postings from a wiki-backed pipeline.

## Source of truth
- `/opt/data/wiki/automation/job-hunting/Rules.md` is the authoritative pipeline state.
- Treat its existing URLs as dedupe keys.
- Only summarize new postings not already present in the pipeline table.

## Reliable cron shape
- `attach_to_session: true` when the user wants replies in the current thread.
- `deliver: origin` for the same-thread workflow.
- Keep `enabled_toolsets` minimal; `terminal` + `file` is enough for wiki-backed briefing jobs.
- Use a concise prompt that says: summarize only new items, stay silent when nothing new exists.

## Output rules
- If no new postings are found, the job should print nothing.
- If there are new postings, emit short Korean bullets with:
  - company
  - position
  - board
  - fit note
  - link
- Keep KST in the human-facing summary.

## Logging
- After creating or updating the job, append a line to `/opt/data/wiki/log.md`.
- Log the job id, recurring schedule, delivery target, and the next run time converted to KST.
- Keep the log append-only; do not rewrite older entries.

## Verification
- `cronjob list` shows the job as active and delivery target as expected.
- The next run time is readable in KST when reported to the user.
- The job stays silent when the pipeline has no unseen URLs.
