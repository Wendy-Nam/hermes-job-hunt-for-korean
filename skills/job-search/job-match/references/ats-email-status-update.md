# Application Status Tracking from Gmail

## Workflow: When USER says "read emails and update status"

### 1. First: Check the Wiki — LIST ALL CANDIDATES INDIVIDUALLY
Read `/opt/data/wiki/automation/job-hunting/Rules.md` for the status schema:
- **대분류**: 예정 / 진행중 / 완료
- **소분류**: 예정{발견·관심·준비} · 진행중{지원준비·지원·서류합격·면접·최종·오퍼} · 완료{탈락·보류·마감}

Search for all postings in `automation/job-hunting/postings/` that are NOT in 완료 status — those are candidates. But **list them explicitly, don't make batch assumptions.** Each posting must be verified individually against email evidence.

⚠️ **Critical pitfall — don't batch-assume:** When USER names a company (e.g. "Coupang"), check EACH posting under that company separately. One Coupang role being a rejection does not mean all Coupang roles are rejections. Update one at a time via `note-set-field.py`.

### 2. Then: Search Recent Gmail
Search Gmail for the **LAST 2-4 WEEKS only** (not all historical email). Use queries like:
```
after:YYYY/MM/DD from:(smartrecruiters OR greenhouse OR lever OR ...)
after:YYYY/MM/DD subject:(application OR regret OR update OR thank you)
```

Don't go back months — USER wants recent updates, not old history.

### 3. Understand ATS Email Types

| Email Pattern | Meaning | Action |
|---|---|---|
| "Thank you for submitting your application... queued for review" | **Acknowledgment only** — not a rejection yet | No status update needed |
| "We regret to inform you... not moving forward" | **Rejection** | Update to 완료/탈락 |
| "We decided to pursue other candidates" | **Rejection** | Update to 완료/탈락 |
| "Your application is being reviewed... will be in touch" | **Acknowledgment / In review** | No status change needed |
| "Let's Stay Connected" or "While your skills are impressive..." | Usually a **soft rejection** | Ask USER for confirmation |

⚠️ **"Thank you for applying" acknowledgment emails are NOT rejections.** They just mean the ATS received the application. Don't update status based on these alone.

### 4. Cross-Reference
Match email content (company name, position) with wiki postings. If the company name in the email doesn't match any wiki posting or the posting doesn't exist, note it to USER.

### 5. Update Fields — NEVER Rewrite the Whole File
Use `note-set-field.py` — never `write_file` to rewrite an existing frontmatter:
```bash
python3 /opt/data/bin/note-set-field.py "<파일명>" 상태="완료" 세부="탈락"
```
Rewriting the whole file with `write_file` breaks Obsidian wikilinks and loses structure.

### 6. Log Changes
Append to `/opt/data/wiki/log.md`:
```
- YYYY-MM-DD KST — [postings] Company Name ×N → 완료/탈락 업데이트
```

### 7. Ask, Don't Assume — and Watch for "Ghost Rejection" Pattern
If you find a matching "thank you for applying" email + a wiki posting in "진행중/지원" status, **read the exact email body and compare against the posting** before updating. Do not rely on the subject phrase alone.

USER's working convention from July 2026: recent ATS emails with "thanks/thank you for applying" phrasing can be **effective rejection/ghost rejection** when they are not plain receipt messages or when USER identifies them as such. Treat USER's correction as authoritative and update the specific posting(s) she names.

Still distinguish true acknowledgments from rejection/ghost patterns:
- "queued for review", "we received/submitted your application", "we will review" → receipt/in-review; do not mark 탈락 unless USER says so.
- "while your skills are impressive", "not moving forward", "decided to pursue other candidates", "unable to proceed", "keep your profile" → 완료/탈락.
- ambiguous "thank you for applying" with no clear receipt/rejection language → ask USER or leave a note, but do not bulk-update.

### 8. Source of Truth is Markdown Frontmatter, Not a DB
When USER asks for a new field (e.g. `우선도`), **add it to the existing markdown frontmatter** — do NOT create a separate SQLite database. The postings are markdown files, not DB rows. Use `note-set-field.py` to add/update fields, or write a batch Python script that reads/writes the frontmatter directly.

### 9. Priority Scoring Field (`우선도`)
- 5점 만점, added to every posting's frontmatter
- Formula: 적합도(0~3) + 연차(0~1) + 트랙(0~1), max 5.0
- Tiers: 4.5+ 🔥 최우선 / 4.0+ ⭐ 우선 / 3.0+ 🔍 보류 / 1.0+ 📋 관찰
- Batch update: write a Python script to parse all `postings/*.md`, calculate, and inject the field. Never create a separate DB.

### Examples from July 2026
- **A사** (예시): "Thank you for submitting... queued for review" → receipt/in-review, NOT rejection unless USER later says otherwise.
- **Coupang CPLB Data Analyst** + **Speak Sales Operations Manager** (7/9): USER explicitly confirmed these are rejections → update only those exact postings to 완료/탈락 via `note-set-field.py`.
- **Coupang Application Submission** and **Coupang 중소형 광고 영업**: USER corrected that these were NOT the rejected Coupang posting → roll back/leave as 진행중/지원. Never update every posting for a company just because one role has a result.