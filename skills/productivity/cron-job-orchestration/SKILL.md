---
name: cron-job-orchestration
description: Hermes-only. Use when creating, updating, or debugging scheduled jobs that must deliver reliably. Handles pinned model/provider config, explicit delivery targets, silent watchdogs, random time windows, and KST reporting.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, scheduling, delivery, watchdog, kst, automation]
    related_skills: [efficient-cron-jobs]
---

# Cron Job Orchestration

## Overview
Use this skill for scheduled jobs that need to run predictably and deliver to the right place.

The main failure mode is not the schedule itself; it is the job configuration around it: unpinned inference config, implicit delivery targets, silent watchdogs that should only emit when triggered, and time-zone confusion when reporting next runs.

## When to Use
- Creating or updating recurring cron jobs
- Debugging jobs that run but do not deliver
- Jobs that should be silent until a condition is met
- Jobs that need random windows instead of fixed intervals
- Any job where the user asks for the next run in KST

## Core Workflow
1. **Confirm the delivery target explicitly.**
   - If the current Hermes thread is the destination, use `attach_to_session=true` and then verify the resolved target with `cronjob list`.
   - For Discord/Telegram/thread delivery, use the fully resolved target, not a guess.
   - Done when the job has a concrete destination that can be resolved at runtime.

2. **Pin model/provider when the job uses inference.**
   - If the scheduler warns about config drift, update the job with explicit provider/model.
   - Do not leave important jobs unpinned after a provider change.
   - Done when the job shows pinned inference config and is no longer skipped for spend protection.

3. **Choose the right execution mode before you debug delivery.**
   - `no_agent: true` means script-only delivery: the script's stdout is the whole message.
   - `no_agent: false` means the agent can read script output as context and generate the final reply.
   - For Discord text-channel delivery, verify the bot/channel wiring and Message Content Intent if the job must read chat text.
   - Use the server's delivery map as a source of truth: keep `hermes-chat` for conversation and send scheduled outputs to purpose channels such as `briefing`, `wiki-log`, `reminders`, `tasks`, and `ops`.
   - Done when you know whether stdout is the final payload or just context, and the target channel is actually reachable.

4. **Use script-only mode for watchdogs that should be silent most of the time.**
   - Empty stdout means silence; non-empty stdout is the delivery payload.
   - Keep the script deterministic and stateful so it can decide when to emit.
   - Done when the script emits only on trigger and stays quiet otherwise.

5. **Implement random windows in the script, not the cron expression.**
   - Cron should tick frequently (for example every 5m).
   - The script should store `last_contact_ts`, `target_ts`, and `notified_for_ts`.
   - Choose a fresh random target inside the window only when the upstream state changes.
   - Done when repeated ticks do not duplicate a message and the trigger window stays bounded.

6. **Report time in KST when the user asks for timing.**
   - Convert UTC timestamps to Asia/Seoul before answering.
   - Do not hand the user raw UTC unless they asked for it.
   - Done when the answer names both the wall-clock KST time and, if helpful, the UTC source.

4. **Implement random windows in the script, not the cron expression.**
   - Cron should tick frequently (for example every 5m).
   - The script should store `last_contact_ts`, `target_ts`, and `notified_for_ts`.
   - Choose a fresh random target inside the window only when the upstream state changes.
   - Done when repeated ticks do not duplicate a message and the trigger window stays bounded.

5. **Report time in KST when the user asks for timing.**
   - Convert UTC timestamps to Asia/Seoul before answering.
   - Do not hand the user raw UTC unless they asked for it.
   - Done when the answer names both the wall-clock KST time and, if helpful, the UTC source.

## Common Pitfalls
1. **Assuming `origin` is always deliverable.**
   - Fix: inspect the job target or switch to an explicit channel/thread target.

2. **Leaving jobs unpinned after config drift.**
   - Fix: update the job with the current provider/model or intentionally repin the old values.

3. **Using cron syntax for randomness.**
   - Fix: keep the schedule fixed and push the randomness into the script state machine.

4. **Treating silent output as failure.**
   - Fix: for watchdogs, silence is success when the trigger has not fired.

5. **Answering schedule questions in UTC when the user clearly wants KST.**
   - Fix: convert first, then answer.

6. **Simulating a proactive opener from vibes instead of the live gate.**
   - Fix: run the actual script or gate first. If stdout is empty, the real job would send nothing now. If the script uses a persisted random target, preserve `last_contact_ts`, `target_ts`, and `notified_for_ts` instead of reseeding on every tick.

## Verification Checklist
- [ ] Delivery target is explicit and runtime-resolvable
- [ ] Inference config is pinned if the job uses a model
- [ ] Silent scripts truly print nothing when not triggered
- [ ] Random window state is persisted and deduplicated
- [ ] Live gate/script is treated as source of truth when docs drift
- [ ] Next-run times are reported in KST when requested

## One-Shot Recipe: What would the proactive job send now?
1. Inspect the live cron job record and script path.
2. Execute the script in the current time zone.
3. If stdout is empty, stop: the real job would stay silent.
4. If stdout contains trigger context, draft from that output and do not invent missing state.
5. When the job uses a randomized window, keep the persisted target state stable across ticks and reseed only when the last-contact timestamp changes.

## Support Files
- See `references/discord-delivery-pitfalls.md` for concrete delivery target failures, attach-to-session usage, and UTC→KST reporting reminders.
- See `references/proactive-openers-session-note.md` for the live-gate / empty-stdout / persisted-random-target pattern observed in practice.
- See `references/job-search-briefing-cron.md` for the recurring job-search briefing pattern (origin delivery + wiki source-of-truth + KST logging).
- See `references/cron-debugging-session-2026-07-07.md` for model-drift pinning, Discord preview suppression, and proactive opener gate fixes from a real session.
