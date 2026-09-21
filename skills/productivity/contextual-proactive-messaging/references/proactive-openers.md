# Proactive Openers / 선톡

Use this when Hermes should start a conversation from live context instead of waiting for a trigger threshold.

## Scan order
1. Live Composio apps first when the opener should reflect workspace state.
   - **Gmail**: scan recent inbox (last 24-48h) for job application responses (서류합격, test invitations, rejections), calendar invites, or time-sensitive opportunities.
2. Obsidian notes second, but treat empty or placeholder notes as missing indexes rather than proof a workflow never existed.
3. Wiki log and secondary state DB third when you need recency or a history of what changed.
4. Recent session history fourth, especially earlier decisions about channels, cron jobs, and prior opener phrasing.
5. If the signal is weak, stay silent instead of inventing a generic opener.

## Output rule
- One short Korean line.
- Prefer a question or a suggestion.
- If there is a useful related fact, share it briefly instead of forcing a question.
- Do not output metadata, rationale, or a long explanation.

## Follow-up flow
A 선톡 that delivers an opportunity (e.g. "서류합격 메일 왔어") may trigger "캘린더에 추가해줘" / "링크 열어줘". Execute the follow-up immediately and report back. The 선톡 is the start of a cycle, not the end.

## Gmail query patterns for job-application discovery

When scanning Gmail proactively for opportunity signals (서류합격, test invitations, interview confirmations):

1. **Use a bounded time window**: `after:YYYY/MM/DD before:YYYY/MM/DD` — aim for the last 24-48h to avoid digging up stale mail.
2. **Check all connected accounts**: when two Gmail accounts are connected, query both in parallel. One may hold job-search emails (user@example.com) and the other personal mail.
3. **Signal keywords to watch for in subject/preview**:
   - 서류합격 / 서류전형 합격 / 합격하셨습니다
   - 영어테스트 / 인성검사 / 과제 안내
   - 면접 / interview / next steps
   - 오퍼시트 / offer / 연봉
   - Thank you for applying / 접수 완료 (confirmation, low priority)
4. **Subjects that need body inspection**: ambiguous subjects like "Application update" or 회사명만 있는 메일. Read the preview body before classifying.
5. **Multi-account**: when a user has two accounts, scan both. The job-search account often holds the opportunity signal; the other may have 보안알림 or newsletters.
6. **Stop at one strong signal**: finding a 서류합격 or test invitation is a powerful 선톡 — report it immediately rather than scanning for more. Multiple weak signals are less useful than one clear opportunity.
