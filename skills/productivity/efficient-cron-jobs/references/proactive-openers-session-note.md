# Proactive opener / 선톡 session note

This note captures the working pattern that emerged during the 2026-07-06 review.

## What to check first
1. Composio-connected apps, if the opener should reflect live workspace state.
2. Obsidian wiki notes, but treat empty or placeholder files as missing indexes rather than proof the feature never existed.
3. Recent session history, especially earlier decisions about channels, cron jobs, and prior opener phrasing.

## What to verify before answering "what would it send now?"
- Inspect the cron job configuration.
- Read the live script that gates output.
- Check recent cron outputs for `silent (empty output)` versus real payloads.
- Only draft a message if the gate actually opened.

## Practical rule
If the gate is silent, answer with silence. Do not invent a plausible opener just because the feature exists.