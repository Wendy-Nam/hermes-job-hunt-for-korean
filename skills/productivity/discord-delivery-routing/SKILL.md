---
name: discord-delivery-routing
description: Use when mapping Hermes or automation outputs to Discord channels, threads, or delivery targets. Keeps conversation, briefing, logs, reminders, tasks, and ops traffic separated by purpose with explicit channel IDs.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [discord, delivery, routing, automation, channels, cron]
    related_skills: [cron-job-orchestration, efficient-cron-jobs, composio-personal-assistant]
---

# Discord Delivery Routing

## Overview
Use this skill when Discord is the delivery surface for Hermes or other automations.

The core rule is simple: **route by purpose, not by knowledge taxonomy**. A wiki schema tells you how to store information. Discord tells you how people should receive it. Those are different layers and should stay different.

This skill is about keeping one conversation lane, several notification lanes, and a clear mapping from automation intent to a specific Discord channel ID.

## When to Use
- Mapping cron jobs to Discord channels
- Choosing where Hermes should talk vs. where it should only notify
- Designing a Discord server layout for an assistant bot
- Reviewing whether a message belongs in chat, briefing, logs, reminders, tasks, or ops
- Fixing a setup where notifications are leaking into the main conversation channel

## Core Rules
1. **Conversation goes in one chat channel.**
   - Do not split normal back-and-forth across topic channels.
   - The chat channel is the default place for direct interaction.
   - Done when users can hold a coherent conversation without hopping channels.

2. **Notifications go in purpose channels.**
   - Scheduled summaries, logs, reminders, task coordination, and ops alerts should each have their own lane when they serve different follow-up actions.
   - Do not dump all automation output into the main chat.
   - Done when each non-chat channel has a single clear job.

3. **Use explicit channel IDs as the source of truth.**
   - Keep a stable routing map with names and IDs.
   - Do not infer targets from a channel name alone.
   - Done when every automation target can be resolved without guessing.

4. **Treat wiki types and Discord channels as different systems.**
   - Wiki `type` values describe storage and retrieval.
   - Discord channels describe delivery and user action flow.
   - Do not mirror one onto the other unless the workflow genuinely improves.
   - Done when channel layout is justified by how messages are used, not by document metadata.

## Recommended Layout Pattern
A practical server layout usually looks like this:

- `hermes-chat` — direct conversation with Hermes
- `briefing` — morning briefings and scheduled summaries
- `wiki-log` — wiki change notifications
- `reminders` — nudges, follow-ups, proactive pings
- `tasks` — active work and project coordination
- `ops` — gateway, cron, watchdog, and incident alerts

This is a baseline, not a law. If two lanes always trigger the same action, merge them. If one lane serves two unrelated actions, split it.

If you are working on the user's current Discord server, treat `references/server-routing-map.md` as the source of truth for channel IDs and category grouping.

## Decision Heuristics
- If the user needs to reply, keep it in the chat lane.
- If the user only needs to read and move on, use a notification lane.
- If the message is about system health or failures, use ops.
- If the message is a follow-up to unfinished work, use reminders or tasks.
- If the message is a digest, use briefing.
- If the message is a change record, use wiki-log.

## Common Pitfalls
1. **Using topic-based channel splits for live conversation.**
   - Fix: keep conversation in one lane and move detail into threads or linked docs.

2. **Routing by wiki type instead of user action.**
   - Fix: ask what the recipient should do next, then choose the channel.

3. **Leaving delivery targets implicit.**
   - Fix: store the Discord channel ID in the job config or routing map.

4. **Letting ops noise pollute the main chat.**
   - Fix: send watchdogs, health checks, and internal alerts to ops.

5. **Creating too many channels too early.**
   - Fix: start with one chat lane and only add a lane when it clearly changes the follow-up behavior.

## Verification Checklist
- [ ] One channel is the designated conversation lane
- [ ] Notification lanes each have a single operational purpose
- [ ] Every target has an explicit Discord channel ID
- [ ] Wiki schema and Discord routing are not conflated
- [ ] New messages land in the lane that matches the next user action

## References
- Pair with `cron-job-orchestration` for scheduled delivery jobs.
- If you maintain a server-specific routing map, keep it in a separate reference file or config so the channel IDs stay current.
