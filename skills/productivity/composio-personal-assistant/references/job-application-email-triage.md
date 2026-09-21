# Job-application email triage

Compact rule set for inbox scanning when the user wants support tracking.

## Decision rule

1. Start with the newest mail only. Use the top N newest inbox items or messages newer than the last read watermark.
2. Prefer subject lines over body text for submission/confirmation detection.
3. Open the body only when the subject is ambiguous or the next action depends on details.
4. Classify only actionable mail: application received, interview invite, rejection, document request, deadline change.
5. Do not rescan old mail unless the user asks for a full audit.

## Practical cues

- Subjects like `Thank you for applying`, `Application received`, `지원해주셔서 감사합니다`, `서류 접수`, `interview`, `next steps` usually signal status changes without body inspection.
- If a thread already has a read watermark or prior message ID, scan forward from there rather than starting over.
- When there are many messages, use a small first pass and hydrate only the shortlist that looks relevant.

## Good output shape

- `회사 / 제목 / 판단 / 한 줄 근거`
- Keep it brief; no inbox dump.
