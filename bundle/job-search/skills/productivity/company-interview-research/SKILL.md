---
name: company-interview-research
description: Use when the user asks you to research a company for interview preparation — deep-dive investigation of company overview, products, tech stack, size/reputation, recent news, and interview insights. Outputs structured markdown in the user's language.
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [interview, research, company, job-prep, Korean, gmail, recruiter]
    related_skills: []
---

# Company Interview Research

## Overview

Perform a structured, multi-source deep-dive investigation of a target company ahead of a job interview. Covers: company description/products, tech stack, size/reputation, recent news, and interview-specific insights. Produces a structured markdown report in the user's language (Korean for Korean companies/interviews).

## Workflow

### Step 1 — Collect company identifiers
Gather from the user or memory: company name (한글 + English), website URL, interview date/time/location, position title.

### Step 1.5 — Hydrate interview details from Gmail before research

If the task starts from a user saying "I have an interview at X" or a vague calendar/company mention, **search Gmail first** — the email contains the richest signal.

**Two sub-patterns:**

**A) Finding the original job posting**
Trigger: user mentions a company they applied to but no clear posting URL.
- Search Gmail for JobKorea/Wanted/Saramin/LinkedIn/ATS notification emails mentioning the company.
- Hydrate the strongest hit and decode the HTML body if the preview is generic.
- Extract the direct posting URL, then use it as the primary source for the rest of the research.
- See `references/job-board-email-triage.md` for the exact pattern.

**B) Hydrating interview scheduling / recruiter screening**
Trigger: user announces "interview got scheduled" or "recruiter screening confirmed."
- Search Gmail across **all connected accounts** — the user may have separate personal/job-search accounts.
- Search query: `"<company>" interview OR screening OR recruiter` (use the exact company name plus context keywords).
- If that returns nothing, fall back to a plain `"<company>"` query covering the last 7-30 days.
- Identify the **recruiter scheduling email** — typically from the company's ATS (Workday, Greenhouse, Lever) or a recruiter's direct email. Key signals:
  - Subject: "Schedule Your Interview", "Interview Invitation", or similar.
  - Sender: recruiter name or ATS bot (`@myworkday.com`, `@greenhouse.io`, `@lever.co`).
  - Body: usually contains a scheduling link (Google Calendar, Calendly, Calendly-style link), recruiter name, role title, and meeting duration.
- Identify any **companion task emails** — pre-interview questionnaires, coding tests, or document requests from the same ATS. Note their status (completed / pending).
- Extract and record:
  - Recruiter name and email
  - Scheduling link (exact URL)
  - Meeting format (Zoom, Teams, phone call) and duration
  - Job ID (from subject or body)
  - Pre-interview task status
- **Update wiki records immediately** after extraction:
  - Create or update `entities/companies/<company>.md` — company entity with full interview details table.
  - Update the posting note in `automation/job-hunting/postings/` via `bin/note-set-field.py` — set 상태/세부 from "지원 완료" to "1차 리크루터 스크리닝콜 예정" (or "면접 예정" for later rounds), and add recruiter name + meeting type to 메모.
  - Append to `log.md` — log the discovery with email subjects and key takeaways.
  - Update `index.md` — add the new company entity link under entities/companies.

### Step 2 — Surface primary sources in parallel
Launch independent fetches together — they don't depend on each other:

1. **Company website** — Try browser_navigate first. If Chrome unavailable (Chrome not found), fall back to:
   - `curl -sL --max-time 15 -A "Mozilla/5.0" "https://www.example.com"` for server-rendered sites
   - For **SPA sites** (React/Vue/Angular where curl only returns a shell HTML), fetch the JS bundle:
     - Extract the JS asset URL from the `<script type="module" crossorigin src="/assets/index-XXXX.js">` tag
     - `python3 -c "import urllib.request; r = urllib.request.urlopen('...js'); data=r.read().decode('utf-8',errors='replace'); print(data)"` to download it
     - Search for: Korean text strings with regex `r'[\uac00-\ud7a3]{2,}'`, business unit definitions like `ko:{name:"...",tag:"...",desc:"..."}`, customer/partner lists, addresses
     - Search for English keywords: `LitePoint`, `partner`, `service`, `solution`, `product`, `authorized`, `official`
   - Extract from meta tags and JSON-LD in the HTML shell

2. **Web search** — Use Composio SEARCH_WEB (preferred) or firecrawl search:
   - Search by Korean name + English name
   - Search by product area + location
   - Search for company size (사원수, 직원수), revenue (매출)
   - Search for recent news (use a recency window)

3. **Korean news search** — Use the `naver-news-search` skill:
   - Query by Korean company name
   - ⚠️ **Pitfall**: Naver API uses full-text matching — short names return false-positive hits. Combine with industry keywords (e.g., `OO테크놀로지 주력분야`). Always verify each result by reading title/description before reporting.

4. **Session memory** — `snow_search` for any prior conversation about this company or interview date

### Step 3 — Triangulate reputation and size
- **Credit reports**: Search for 업계신용평가, 씽크존, 기업정보 reports (may be behind paywalls; note the existence and CEO name)
- **LinkedIn**: Search for employee profiles to gauge headcount and team structure
- **Customer lists**: Extract from JS bundle, website, or case studies — these signal market position
- **Competitors**: Search for industry peers (e.g., competitors in wireless test, semiconductor test, etc.)

### Step 4— Check for job posting details
Search by position title + company name. If the exact posting isn't found, infer role responsibilities from the company's product portfolio.

### Step 5 — Structure the output report

Write the report in **the user's language** (Korean for Korean interviews). Structure:

```
# Company Name (한글/English) — Interview Prep Summary

> 연구일시, 면접 일시/장소

---

## 1. 회사 개요 (Company Overview)
테이블: 회사명, 대표자, 주소, 연락처, 웹사이트, 업력

## 2. 사업 내용 (Products & Services)
핵심 사업 요약문, 부문별 테이블 (사업부 | 브랜드 | 설명)

## 3. 주요 파트너 및 고객사
파트너사 리스트, 주요 고객사

## 4. 기술 스택 (Tech Stack)
제품/솔루션 관련 기술, 자사 웹사이트 기술 스택

## 5. 회사 규모 및 평판
사원수 (가용시), 경쟁사 비교, 시장 내 포지셔닝

## 6. 최근 뉴스 및 동향
뉴스가 없으면 "확인되지 않음"이라 명시, 업데이트 날짜

## 7. 면접 인사이트 (Interview Insights)
직무 이해, 면접 준비 포인트, 예상 질문, 면접 팁

## 8. 출처 (Sources)
```

## Common Pitfalls

1. **Browser unavailable** — Chrome is frequently not installed. Don't waste time retrying browser_navigate. Go straight to curl + python urllib for HTML and JS bundle analysis.
2. **SPA sites with no SSR** — The HTML shell is just `<div id="root"></div>` + a JS bundle link. The real content is in the JS. Extract it by downloading the bundle and grepping for structured data blocks (ko:{...}, customer lists, partner lists).
3. **False-positive news results** — Naver news search returns substring matches. Always verify title/description before including. Combine company name with industry keywords.
4. **Credit/employee data behind paywalls** — Note what exists (e.g., "신용평가 보고서 존재") but don't fabricate numbers. Be honest: "정확한 사원수는 공개되지 않음."
5. **Outdated website content** — Check the HTTP `last-modified` header or meta timestamps. Sites can be months old.
6. **Replit-hosted sites** — `replit-dev-banner.js` in the HTML is a tell. Replit sites are typically built by the company itself, not a professional web team. Expect simpler architectures.
7. **Not all Gmail accounts searched** — Users commonly have separate personal and job-search accounts. The interview email may live in the latter. Always iterate all connected accounts before concluding no email exists.
8. **Narrow Gmail query misses the target** — `"<company>" interview OR screening OR recruiter` can return zero hits if the email body lacks those exact keywords. Always fall back to a plain `"<company>"` query covering the last 30 days.
9. **HTML body not decoded** — ATS emails (Workday, Greenhouse) ship multipart HTML. Strip tags and decode entities before reading; extract scheduling URLs via regex.
10. **Scheduling link mistaken for confirmed event** — The email says "choose a slot", not "your interview is at TIME". Mark wiki status as "예정" but keep the slot unconfirmed until the user picks and the recruiter confirms the time.
11. **Companion tasks overlooked** — Questionnaire or coding test emails arrive at similar timestamps with related subject lines. Check 10-15 minutes before the scheduling email for companion messages sharing the same Job ID.

## Verification Checklist

- [ ] Company website analyzed (HTML shell or JS bundle)
- [ ] At least one web search completed (Composio or firecrawl)
- [ ] Korean news searched (Naver news skill, if applicable)
- [ ] Session memory checked (snow_search)
- [ ] Interview details confirmed (date, time, address, position)
- [ ] All connected Gmail accounts searched for recruiter/ATS emails
- [ ] Scheduling link extracted and confirmed (not a generic "reply to schedule")
- [ ] Companion task emails noted (Questionnaire, coding test, etc.)
- [ ] Wiki records updated: company entity, job-applications status, log, index
- [ ] Output report written in user's language with all 8 sections
- [ ] Report saved to a file with descriptive name
- [ ] Sources cited (URLs) for key claims

## Reference Files

- `references/company-research-example.md` — worked example (fictionalized Korean B2B company).
- `references/job-board-email-triage.md` — finding original job posting URLs from board notification emails.
- `references/recruiter-email-triage.md` — concrete walkthrough: hydrating Unity SDR screening email from Gmail.