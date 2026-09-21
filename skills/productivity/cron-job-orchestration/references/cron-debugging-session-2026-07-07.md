# Cron debugging notes — 2026-07-07

## Model drift / unpinned job
- A scheduled job created under `gpt-5.4-mini` was skipped after the global provider default moved to `gpt-5.5`.
- Scheduler error text: `Skipped to prevent unintended spend: global inference config drifted since this job was created ... and this job is unpinned.`
- Fix: explicitly pin the job with `cronjob action=update ... provider=<provider> model=<model>` before rerunning.

## Discord link preview suppression
- When sending URLs in cron-delivered chat, wrap each URL in angle brackets: `<https://example.com>`.
- Avoid markdown links (`[text](url)`) if you want to suppress Discord previews.
- Keep one URL per line; do not append punctuation or extra characters after the closing `>`.

## Proactive opener gate
- For 선톡-style jobs, ignore empty assistant bookkeeping rows when detecting the last real contact.
- Treat `COALESCE(TRIM(content), '') != ''` as the baseline filter for last-contact detection.
- If the job uses a random delay window, persist `last_contact_ts`, `target_ts`, and `notified_for_ts` so repeated ticks do not reseed unnecessarily.
