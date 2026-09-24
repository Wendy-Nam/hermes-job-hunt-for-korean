---
name: cover-letter-pdf
description: "Generate a professional A4 PDF cover letter for a job posting. Uses a minimalist HTML template with clean typography and converts to PDF via Cloudlayer.io (CLOUDLAYER_CONVERT_HTML_TO_PDF_V2). 영어 기본, 한국어 지원."
version: 1.1.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 커버레터, cover-letter, pdf, 지원, 영문]
    related_skills: [linkedin-search, ats-search, job-match]
---

# A4 PDF 커버레터 생성 (cover-letter-pdf)

지원하려는 공고에 대한 커버레터를 A4 PDF로 생성한다.

## When to use
- 사용자가 커버레터 작성을 요청할 때
  - "커버레터 써줘", "cover letter 작성해줘"
  - "Acme SDR 커버레터"
  - "영문 커버레터 PDF로 만들어줘"
  - 특정 공고명/회사명 포함 문장
- 구직 포스팅 노트 기반으로 작성 필요할 때
- 지원 전 PDF 제출용 문서가 필요할 때

## Language rule
- **기본값: 영어.** 외국계/글로벌 회사는 무조건 영어.
- 한국 회사가 명확할 때만 한국어. 애매하면 영어.
- GPT 위임 시 goal에 언어를 명시할 것.

## How — Workflow

### Step 1. 포스팅 식별
- 사용자가 회사/포지션을 명시하면 → `automation/job-hunting/postings/`에서 검색
- 명시하지 않으면 → 질문으로 확인
- 포스팅 노트의 frontmatter: `상태`, `적합도`, `티어` 참고하여 어조 결정

### Step 2. 이력서 로드
- 영문: `automation/job-hunting/resume/en-sales.md`
- 국문: `automation/job-hunting/resume/kr-sales.md` (없으면 en 기반)
- 태스크 위임 시 모든 컨텍스트를 넘길 것

### Step 3. 커버레터 내용 생성 (반드시 delegate_task 사용)
- **절대 내가 직접 생성하지 않는다** — 반드시 `delegate_task`로 GPT에 위임
- Goal에 다음 지침 포함:
  - JD + 이력서 매칭 분석
  - 언어: 영어 (기본) 또는 한국어 (명확한 한국 회사)
  - 어조: 두괄식 시작, 간결+구체적, 겸손한 어조, em dash(—) 사용 금지
  - 구체적 수치·성과 포함 (본인 핵심 프로젝트·성과)
  - 문장은 짧게, 불필요한 형용사 배제
  - 출력 형식: HTML ready — `<p>`, `<ul><li>` 태그로 이미 마크업되어 있어야 함

### Step 4. HTML 템플릿에 내용 주입
- **⚠️ 주입 전 이스케이프(보안)**: 평문 변수(NAME·COMPANY·POSITION·CONTACT·DATE)는 `<` `>` `&`를 HTML 이스케이프. `{{BODY}}`는 위임 GPT가 만든 `<p>`/`<ul><li>`/`<strong>`만 허용하고, JD 원문의 마크업·스크립트를 그대로 옮기지 마라 — 완성 HTML은 외부(Cloudlayer 헤드리스 브라우저)에서 렌더되므로 주입되면 실행될 수 있다.
- 템플릿 경로: `SKILL_DIR/templates/cover-letter.html`
- 변수 매핑:
  | 변수 | 설명 | 예시 |
  |---|---|---|
  | `{{NAME}}` | 지원자 이름 | Your Name |
  | `{{CONTACT}}` | 연락처 한 줄 | City, Country · you@example.com |
  | `{{DATE}}` | 생성일 | July 18, 2026 |
  | `{{COMPANY}}` | 회사명 | Acme |
  | `{{POSITION}}` | 포지션 | Sales Development Representative - Korea |
  | `{{SUBJECT}}` | ~~제목 (선택. 생략 가능)~~ | ~~Re: Sales Development Representative~~ |
  | `{{BODY}}` | 본문 HTML | `<p>...</p>` `<ul>...</ul>` 포함 |
  | `{{CLOSING_LINE}}` | 마무리 문장 | I look forward to discussing how I can contribute to your team. |
  | `{{REGARDS}}` | 인사말 | Best regards, |
  | `{{SIGN_NAME}}` | 서명 이름 | Your Name |
  | `{{SIGN_CONTACT}}` | 서명 연락처 | you@example.com |

### Step 5. PDF 변환 (CLOUDLAYER_CONVERT_HTML_TO_PDF_V2)
- tool_slug: `CLOUDLAYER_CONVERT_HTML_TO_PDF_V2`
- arguments:
  ```json
  {
    "html": "<완성된 HTML 문자열>",
    "format": "a4",
    "margin": {
      "top": "0mm",
      "bottom": "0mm",
      "left": "0mm",
      "right": "0mm"
    },
    "printBackground": true,
    "preferCSSPageSize": true,
    "storage": true,
    "filename": "Cover_Letter_{{COMPANY}}_{{POSITION_SHORT}}.pdf"
  }
  ```
- `preferCSSPageSize: true` + `margin: 0mm` = HTML @page CSS가 우선 적용됨
- `storage: true` → Cloudlayer에 저장, 응답 `assetUrl`이 PDF 다운로드 URL

### Step 6. 결과 전달
- PDF 다운로드 URL을 사용자에게 전달
- 커버레터 텍스트는 채팅에도 함께 표시 (PDF 링크만 던지지 말 것)

## Templates
- `SKILL_DIR/templates/cover-letter.html` — 미니멀리즘 A4 템플릿 (영문 기본, 한글 공용)
  - 상단 얇은 악센트 라인
  - Inter / Helvetica Neue 폰트
  - 30mm 상단 여백, 25mm 좌우
  - 깔끔한 sender → date → recipient → subject → body → closing → signature 구조

## Verification
- [ ] Cloudlayer API로 HTML→PDF 변환 성공 (A4, 1페이지)
- [ ] 한글/영문 혼합 폰트 렌더링 정상
- [ ] margin/padding 깨짐 없음
- [ ] 상단 악센트 라인 출력 확인
