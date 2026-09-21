# Proactive Openers / 선톡

Use this when the user wants the assistant to start a conversation from context, not from a trigger threshold.

## Goal
- Inspect available context first: Composio apps, Obsidian wiki, and recent session history.
- Infer what the user is probably doing, stuck on, or about to need.
- Produce a short, natural Korean opener, question, suggestion, or brief info share.

## What this is not
- Not a silent trigger script that outputs only when a threshold is crossed.
- Not a metadata dump.
- Not a long explanation of why the message was chosen.

## Heuristics
- Prefer one concrete, low-friction opener tied to recent context.
- If there is a likely next action, ask about that.
- If the context suggests a missing step, surface that step.
- If the evidence is weak, stay quiet instead of forcing a random opener.

## Output shape
- Default to 2-3 short lines when it reads naturally.
- One line only when the message is tiny and time-sensitive.
- Korean only.
- Sounds like a real chat message.
- Short enough to send immediately.

## Example styles
- "오늘 그거 이어서 볼까?\n필요하면 내가 흐름 잡아줄게."
- "지금 막힌 거 있으면 같이 풀어보자.\n짧게 정리해도 돼."
- "아까 얘기하던 거, 지금 좀 더 잡아볼까?\n딱 필요한 만큼만 같이 보자."