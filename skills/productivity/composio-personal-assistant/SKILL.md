---
name: composio-personal-assistant
description: "Personal assistant briefings via Composio-connected apps (Google Calendar, Gmail) — calendar lookaheads, email triage, thread drilling, and multi-app workflows. Uses Composio MCP tools for auth and execution."
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [Composio, Google, Calendar, Gmail, Personal Assistant, Briefing, Korean]
    related_skills: []
---

> **전제**: Composio MCP 연결 — Hermes 전용 아님. MCP를 지원하는 에이전트(Hermes·Codex·Claude Code 등) 어디서든 동일하게 동작한다.

# Composio Personal Assistant

Use Composio-connected apps to provide personal assistant briefings — calendar scheduling, email inbox triage, LinkedIn profile/context reads when connected, and deep-dive analysis of threads.

### Job-application email triage

When the user wants application-status tracking from Gmail, prefer a **new-mail-first** pass instead of rescanning the whole mailbox:

1. Fetch the newest inbox or matching messages first, ideally the top N most recent messages.
2. Use the last-read point, thread history, or a recent timestamp watermark to avoid rereading older mail.
3. Treat the **subject line** as the primary signal for submission/confirmation detection; open the body only when the subject is ambiguous or the next step depends on details.
4. Summarize only actionable updates: new application confirmations, interview invites, rejections, document requests, or deadline changes.
5. Stop once the new items are classified; do not expand into full inbox archaeology unless the user explicitly asks for it.

See `references/job-application-email-triage.md` for the compact decision rule.

## When to use

- User asks "what's my schedule this week" or "read my calendar"
- User asks "check my recent emails" or "what's new in my inbox"
- User asks about a specific person/thread in their email (e.g. "how much coaching with my coach?")
- User wants a combined "briefing" of calendar + email status
- User wants the assistant to maintain a live job-application / interview status list from Gmail + Calendar and keep a wiki backup in sync
- User wants a proactive, context-based opener/check-in that uses available workspace context instead of a rigid trigger gate
- Korean-speaking users using Asia/Seoul timezone

## Prerequisites

- Composio connections already active for the target apps (check with `mcp_composio_COMPOSIO_SEARCH_TOOLS`)
- Apps typically needed: `googlecalendar`, `gmail`
- LinkedIn is optional when the user wants profile/context or company-scoped reads via an active connection
- For proactive openers / 선톡-style prompts, read `references/proactive-openers.md`
- For LinkedIn read-access behavior and permission-limited responses, see `references/linkedin-read-access.md`

## Proactive Openers / 선톡

Use this mode when the user wants the assistant to start a chat from context instead of waiting for a trigger threshold.

1. Inspect available context in this order: Composio apps, Obsidian wiki, recent session history. Done when you can name the likely next concern in one sentence.
2. Infer the user's current state or next useful question from the freshest signal. Done when one concrete hypothesis beats a vague summary.
3. Draft a short Korean opener that sounds like a real chat message. Use 2-3 short lines by default when that feels natural; keep it brief enough to send as-is.
4. If the user explicitly asks for more room, or says the one-liner feels too cramped, expand to 2-3 short lines unless the channel truly demands brevity.
5. If the evidence is weak, do not force a random opener. Done when the safest output is silence.

Keep the opener lightweight: one question, one suggestion, or one brief info share — never a metadata dump.

## Workflow

### Step 0: Search tools and create session

Always start a new Composio session:

```
mcp_composio_COMPOSIO_SEARCH_TOOLS with:
  session: {generate_id: true}
  queries: [{use_case: "read google calendar events"}, {use_case: "fetch recent gmail emails from inbox"}]
```

Note the returned `session.id` — pass it to ALL subsequent composio calls.

**Important:** The search response includes `toolkit_connection_statuses`. Check `has_active_connection: true` before proceeding. If not connected, use `COMPOSIO_MANAGE_CONNECTIONS` first.

### Step 1: Anchor time in user's timezone

Before computing relative dates (this week, next week), get the current time in the user's timezone:

```
mcp_composio_COMPOSIO_MULTI_EXECUTE_TOOL with:
  tool_slug: GOOGLECALENDAR_GET_CURRENT_DATE_TIME
  arguments: {timezone: "Asia/Seoul"}  # or user's timezone
```

Use the returned `current_datetime` to calculate window boundaries:
- This week: Monday 00:00 to next Monday 00:00
- Next week: next Monday 00:00 to the Monday after

### Step 2: Fetch calendar events (parallel)

Fetch this week and next week **in parallel** with a single multi-execute call:

```
mcp_composio_COMPOSIO_MULTI_EXECUTE_TOOL with:
  tools: [
    {tool_slug: GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS,
     arguments: {time_min: "2026-07-06T00:00:00+09:00", time_max: "2026-07-13T00:00:00+09:00",
                 response_detail: "minimal", single_events: true}},
    {tool_slug: GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS,
     arguments: {time_min: "2026-07-13T00:00:00+09:00", time_max: "2026-07-20T00:00:00+09:00",
                 response_detail: "minimal", single_events: true}}
  ]
```

- Use `response_detail: "minimal"` to get `summary_view` — much lighter than full detail
- `summary_view` has: title, start, end, is_all_day, calendar, event_id, display_url
- Always pass RFC3339 timestamps with timezone offset (e.g. `+09:00`)
- For Korean users, the `#대한민국의 휴일` calendar is included automatically — it shows public holidays

### Step 3: Fetch recent emails

```
mcp_composio_COMPOSIO_MULTI_EXECUTE_TOOL with:
  tool_slug: GMAIL_FETCH_EMAILS
  arguments: {max_results: 20, verbose: true, include_payload: false, user_id: "me"}
```

Pitfalls:
- Set `max_results` to a practical number (15-20 for a briefing, up to 500 for bulk)
- `include_payload: false` keeps responses lightweight (metadata only)
- Large responses (>130K tokens) are saved to a remote sandbox file — you'll see a `remote_file_info` block with a path like `/mnt/files/mex/find.json`. When this happens, use COMPOSIO_REMOTE_WORKBENCH to process it.

### Step 4: Process large email responses

When `remote_file_info` is returned:

```python
# In COMPOSIO_REMOTE_WORKBENCH:
import json
file_data = json.load(open("/mnt/files/mex/find.json"))
result_data = file_data['results'][0]['response']['data']
messages = result_data.get('messages', [])
```

Always sort by `messageTimestamp` descending for recency.

### Step 5: Drill into specific threads

To get full details of a specific sender/thread, use `GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID` with `format: "metadata"`:

```
mcp_composio_COMPOSIO_MULTI_EXECUTE_TOOL with:
  tools: [
    {tool_slug: GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID,
     arguments: {message_id: "19f2d5f6e48de390", format: "metadata"}}
  ]
```

- `format: "metadata"` returns headers (subject, from, to, date, references) + preview body — ideal for thread analysis
- The `References` header contains every message-id in the thread's history — count them to estimate thread depth
- `In-Reply-To` shows the parent message in the thread
- `labelIds` with `IMPORTANT` or `UNREAD` signal priority

### Step 6: Compose the briefing

Present results in a clear, scannable format:

**Calendar:**
- Group by week, then by day
- Show time range + title + type (면접/미팅/스터디/공휴일)
- Highlight busy days (multiple events on the same day)

**Email:**
- Categorize by theme (면접/채용, AI/IT, 기타)
- For important threads, summarize the conversation flow
- Show financial details (정산, 결제) clearly with amounts

### Calendar maintenance / job-search scheduling

Use this mode when the user asks to verify that known job-search events exist and create missing ones.

1. Fetch the full requested calendar window first with `GOOGLECALENDAR_EVENTS_LIST` on the target calendar, `singleEvents: true`, `orderBy: "startTime"`, explicit `timeMin/timeMax` with the user's timezone offset, and a high `maxResults`. Done when every event in the requested date range is available for client-side dedupe.
2. Check for duplicates by normalized title, company keyword, and start time before writing. Do not rely only on exact title matches; many job events are imported with recruiter-provided titles. Done when each requested event is classified as present or missing.
3. For missing interview/coffee-chat events, create a normal timed event. If an actual meeting URL is unknown, leave a clear source note in `description`. Be aware `GOOGLECALENDAR_CREATE_EVENT` defaults `create_meeting_room: true`; this can create a new Google Meet link even when the real meeting link should come from the recruiter.
4. For deadline/reminder events, set `create_meeting_room: false` and usually `transparency: "transparent"` so the event does not imply a call or block time unnecessarily. Include the evidence and any uncertainty in the description.
5. After writes, re-run `GOOGLECALENDAR_EVENTS_LIST` for the same window and verify the new events are present. Done only after the final report includes every job-search event found plus whether each has Google Meet/Zoom/location/join-link information.

### Gmail deadline extraction for offers

When the user asks for an offer reply deadline, do not stop at the message list preview.

1. Search Gmail with company-name variants and offer/deadline terms using the requested account ID when multiple Gmail accounts are connected. Done when candidate message IDs and thread IDs are collected.
2. Hydrate the candidate offer message and thread using `GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID` and/or `GMAIL_FETCH_MESSAGE_BY_THREAD_ID`. If output is offloaded, parse the saved response in the remote workbench instead of relying on truncated previews.
3. Read the full thread text for explicit validity windows, extension requests, and employer confirmations. A candidate's extension request is evidence of a target date, but distinguish it from an employer-confirmed deadline in the calendar description.
4. If the decisive deadline may be in an attached offer sheet, download the attachment with `GMAIL_GET_ATTACHMENT` and inspect it when practical. If extraction is unavailable or inconclusive, record that limitation in the event/report rather than inventing a precise source.

## Token Optimization

Composio MCP는 프롬프트에 **메타 도구 7개만** 올린다 — 500+ 앱 도구는 `COMPOSIO_SEARCH_TOOLS`로 런타임에 동적 조회되므로 프롬프트를 부풀리지 않는다(따라서 도구 필터링은 불필요). 근거·정본: `skills/productivity/hermes-token-optimization/references/composio-mcp-architecture.md`.

### Cron Job enabled_toolsets

When a briefing runs as a cron job, restrict toolsets:

```
enabled_toolsets: ["web", "terminal", "file"]
```

No need for browser, vision, image_gen, or delegation in a data-collection cron.

### Always Prefer minimal response_detail

Already the default in this skill's Step 2. `response_detail: "minimal"` returns `summary_view` (title, start, end, is_all_day) — much lighter than full event objects. Only escalate to `full` when the user explicitly asks for attendee lists or conference links.

### Composio vs Google Workspace Direct

**Stick with Composio** when Gmail/Calendar/Drive are already connected — zero setup, cross-app workflows in one call. 메타 도구 7개만 올라가는 구조라(위 Token Optimization) 토큰 오버헤드는 무시할 수준이다.

**Switch to Google Workspace direct** (`google-workspace` skill) only if you want zero MCP schema injection at all and are willing to do the ~5 min OAuth setup. For most personal assistant use, Composio is the practical sweet spot.

## Korean-language specific notes

- Korean users often use casual greetings (ㅎㅇ, ㅇㅇ) — match tone
- Korean calendars include `ko.south_korea#user@example.com` (대한민국의 휴일) automatically
- Korean mail subjects often contain [브랜드명] prefixes (e.g. [잡코리아], [원티드], [사람인])
- Address the user by their Korean name if visible in email signatures
- 반복 등장하는 코칭/서비스 브랜드는 발신자 도메인으로 식별해 카테고리에 반영

## Pitfalls

1. **Timezone mismatch**: Always anchor with `GOOGLECALENDAR_GET_CURRENT_DATE_TIME` in the user's timezone before computing date windows. Off-by-one-day errors are common if you use UTC alone.
2. **Large response handling**: GMAIL_FETCH_EMAILS with 20+ results may save to a remote file. Always check for `remote_file_info` and use COMPOSIO_REMOTE_WORKBENCH when present.
3. **Empty events ≠ no events**: In minimal mode, `events: []` is normal — the data is in `summary_view`, not `events`. Check `summary_view` first.
4. **Pagination**: `GMAIL_FETCH_EMAILS` has `nextPageToken`. For a briefing, 15-20 emails is usually enough; no need to paginate unless the user asks for more.
5. **Message IDs for thread drilling**: Get `messageId` from the GMAIL_FETCH_EMAILS response first. Do not guess or fabricate IDs — the API returns hex strings like `19f2d5f6e48de390`.
6. **Session management**: Always pass the same `session_id` returned by COMPOSIO_SEARCH_TOOLS to all subsequent composio meta tool calls in the same workflow.
7. **Response_detail minimal vs full**: For briefings, minimal is sufficient. Only use full when the user asks for detailed event descriptions, attendees, or conference links.

## References

- `references/application-state-management.md` — Gmail/Calendar workflow for job applications, interview drops, and wiki-backed status lists
- `references/proactive-openers.md` — opener workflow and output shape
- `references/calendar-email-briefing-example.md` — full transcript example from a real session (Korean user, this week + next week briefing + email triage)
- `references/linkedin-read-access.md` — authenticated LinkedIn self-info / person-info behavior and permission limits