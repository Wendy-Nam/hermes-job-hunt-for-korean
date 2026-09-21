# Application State Management via Gmail + Calendar

Use this when the user wants the assistant to keep job applications, interview progress, or offer status in sync across Gmail, Calendar, and the wiki.

## Core pattern
1. Treat Gmail as the canonical intake stream for application receipts, interview invites, test instructions, and offer emails.
2. Treat Calendar as the canonical source for scheduled interviews and meetings.
3. Status lives in each posting note's frontmatter under `automation/job-hunting/postings/` (update via `bin/note-set-field.py`, never rewrite frontmatter wholesale). Use only stable status labels:
   - 지원 완료
   - 서류 합격
   - 인터뷰 예정
   - 오퍼 검토중
   - 드랍 / 보류
4. Keep sensitive values out of the backup note. Store only the minimum needed to manage status.

## Exact-match deletion rule
- When the user asks to drop or delete an interview from Calendar, search for the event by the company / role / exact title first.
- Delete only if the match is strong and unambiguous.
- If the search returns no clear match, report that it was not found instead of guessing or deleting a similar event.

## Inbox-to-list workflow
- Pull recent Gmail application-related messages.
- Extract company, role, and current status from the subject/preview/body.
- Update the backup note in one pass so the list stays current.
- If the user provides a new application / interview / offer, append it to the list immediately and mark its status.

## Useful status labels
- 지원 접수
- 서류 합격
- 테스트 대기
- 면접 예정
- 면접 완료
- 오퍼 수령
- 오퍼 검토중
- 드랍

## Pitfalls
- Do not invent calendar deletions from partial names.
- Do not overwrite the application list with transient task chatter.
- Do not store raw email contents or personal contact details in the backup note.
