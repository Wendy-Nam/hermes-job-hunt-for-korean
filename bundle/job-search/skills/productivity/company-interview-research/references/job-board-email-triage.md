# Job-board email triage for interview research

Use this when the user wants interview/company prep from a calendar event, Gmail clue, or job-board notification.

## Signals worth checking
- Calendar event title contains company name and interview type, but no posting link.
- Gmail contains JobKorea / Wanted / Saramin notifications for the same company.
- The email preview only shows generic text, but the body likely embeds the job URL and company facts.

## Practical workflow
1. Search Gmail with company name + interview keywords.
   - Good query shapes:
     - `("회사명" OR company) (면접 OR 인터뷰 OR 지원완료 OR 이력서 열람)`
     - Add job-board names if needed: `jobkorea OR wanted OR saramin`.
2. If results look relevant, hydrate the message.
   - Prefer message-level hydration first.
   - For HTML emails, decode the MIME body and search the raw HTML for:
     - `jobkorea.co.kr/Recruit/GI_Read/`
     - company homepage links
     - `지원완료`, `이력서 열람`, `공고보기`
3. Extract only the durable clues:
   - public posting URL
   - title
   - deadline / pay / location if present in page metadata
   - company overview from the notification body (industry, founding year, employee count, site)
4. If the public posting page is reachable, trust its `<meta>` tags first.
   - JobKorea often exposes enough data in `og:title` / `og:description` even when the page body is JS-heavy.
5. Turn the posting into interview prep points:
   - role-specific technical/operational concerns
   - likely screening questions
   - company/domain background to study

## Gotchas
- Gmail previews can be misleadingly generic. The real link is often only visible after hydration + HTML decode.
- JobKorea alert mails frequently contain both the company profile blurb and the direct job URL.
- Do not assume the calendar event name alone identifies the exact posting; confirm from the email or public page when possible.
- If no direct mail hit exists, use the company name from the event and jump straight to public web search / job board search.
