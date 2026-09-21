# Recruiter Email Triage — Concrete Walkthrough

## Scenario: User says "OO테크에서 SDR 스크리닝콜 잡혔어"

This walkthrough follows the Step 1.5-B pattern for hydrating interview scheduling details from Gmail.

### Step 1: Search both connected accounts

The user has two Gmail accounts: a personal one and a job-search one. Always check both.

```bash
# Account 1 (personal): user@example.com — searched as GMAIL_FETCH_EMAILS with account="gmail_hulk-maty"
query = '"OO테크" interview OR screening OR recruiter'

# Account 2 (job-search): user@example.com — searched as GMAIL_FETCH_EMAILS with account="gmail_odin-apoda"
query = '"OO테크" interview OR screening OR recruiter'
```

If the narrow query returns nothing (as it did here — the search returned empty for both), widen:

```bash
query = '"OO테크"'  # plain company name, no context keywords
```

Account 2 returned 7 results, filtered down to 2 key emails.

### Step 2: Identify the scheduling email

**Primary email — Schedule Your Interview**
- Subject: `[OO테크] Schedule Your Interview`
- Sender: `Jane <user@example.com>` — note: `@myworkday.com` domain signals Workday ATS. Recruiter name is J. Doe.
- Body text extracted from HTML: "Hi USER, We're excited you applied for the Sales Development Representative job and would like to talk a little more about your experience and interests. Please help me by choosing a 30 min slot for a quick zoom chat via this link."
- Scheduling link found in HTML: `https://calendar.example.com/<booking-link>`

**Secondary email — Questionnaire Task**
- Subject: `A Task Awaits You: Complete Questionnaire - Interview: USER — JOBREQ-0000000 Sales Development Representative`
- Sender: `OO테크 Technologies <user@example.com>`
- Body: "Please log into the Workday system to complete this action."
- Status: **pending** — the questionnaire is not yet completed.

### Step 3: Extract structured data

| Field | Value |
|-------|-------|
| Company | OO테크 Technologies |
| Position | Sales Development Representative |
| Job ID | JOBREQ-0000000 |
| Stage | 1차 리크루터 스크리닝콜 |
| Recruiter | J. Doe (제인) <user@example.com> |
| Format | Zoom, 30min |
| Scheduling link | https://calendar.example.com/<booking-link> |
| Pre-task | Workday Interview Questionnaire (미완료) |

Note: the scheduling link points to Google Calendar appointment slots (calendar.app.google/...), not Calendly. This means the recruiter set up availability blocks in Google Calendar.

### Step 4: Update wiki records in order

1. **Create** `entities/companies/<company>.md` with full frontmatter and interview details table
2. **Update** the matching posting note in `automation/job-hunting/postings/` via `bin/note-set-field.py` — e.g. `세부="1차 리크루터 스크리닝콜 예정"`, `메모="J. Doe, 30min Zoom, 스케줄링 링크 도착"`
3. **Append** to `log.md` with timestamp, email subjects, and key info extracted
4. **Update** `index.md` — add the new entity link under `entities/companies/`

### Common pitfalls for this pattern

- **Narrow query fails first time**: The combined query `"OO테크" interview OR screening OR recruiter` returned 0 results. The broad `"OO테크"` query returned 7 — the interview email's body didn't contain the keywords "screening" or "recruiter". Always fall back to a plain company-name query when narrow fails.
- **Two-account blind spot**: The interview email was in the job-search account (`user@example.com`), not the primary personal account. Always search all connected accounts.
- **HTML body needs stripping**: The full messageText is HTML. Extract URLs with regex `https?://[^\s"'<>]+`, and strip tags/replace entities for human-readable body text.
- **Scheduling email is not the same as confirmation**: The email says "choose a 30 min slot" — the interview is NOT yet scheduled. The wiki status reflects "예정" but the slot still needs picking. Don't mark a calendar event as confirmed until the user has actually chosen and the recruiter confirmed the time.
- **Companion task email is easy to overlook**: The questionnaire task arrived 10 minutes *before* the scheduling email. Check adjacent timestamps (same date, similar subject) so you don't miss pending pre-interview work.