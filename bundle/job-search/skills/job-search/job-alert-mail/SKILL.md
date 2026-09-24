---
name: job-alert-mail
description: "Use to harvest personalized job recommendations from platform alert emails (LinkedIn/원티드/사람인 맞춤 공고 알림) via Composio Gmail, and merge them into the job-search pipeline. 플랫폼 추천엔진의 출력을 메일로 수신해 흡수한다. 채용 알림 메일 파싱."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, 알림, 이메일, gmail, composio, 추천, job]
    related_skills: [wanted-search, linkedin-search, kr-search, job-match, apify-search]
---

> **전제**: Composio MCP 연결 — Hermes 전용 아님. MCP를 지원하는 에이전트(Hermes·Codex·Claude Code 등) 어디서든 동일하게 동작한다.

> **연차·시니어·제외 기준의 SoT는 `search-profile.yaml`(수집)과 `automation/job-hunting/Rules.md`(판정)다. 이 문서의 수치는 참고용 — 기준 변경은 저 두 파일에서만.**

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부[발견/관심/준비/지원준비/지원/서류합격/면접/최종/오퍼/탈락/보류/마감]·게시일·링크·추가링크1~2·메모·우선도, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# 잡 알림 메일 수확 (job-alert-mail)

**왜 이 스킬인가**: 링크드인·원티드·사람인의 "맞춤 공고 알림 메일"은 각 플랫폼 추천엔진
(프로필·행동 기반 협업필터링)의 출력물이다. 우리가 못 만드는 개인화 추천을 **메일 파싱만으로
공짜로 흡수**한다. 로그인 스크래핑 불필요 → 계정 차단 리스크 0.

## 전제 조건
1. 사용자가 각 플랫폼에서 **채용 알림 이메일을 켜둬야** 한다 (링크드인 Job Alert, 원티드 맞춤공고 알림, 사람인 맞춤공고 메일).
2. Composio에 **Gmail이 연결**돼 있어야 한다. 미연결이면 이 스킬은 조용히 건너뛰고, 대화 중이면 사용자에게 연결을 안내한다.
   - ✅ 2026-07-07 확인: Gmail active 계정 2개 연결됨 (default 포함) — 즉시 사용 가능.

## 워크플로 (Composio MCP)
1. **도구 확인**: `COMPOSIO_SEARCH_TOOLS`로 "gmail fetch emails" 계열 도구를 찾는다 (예: `GMAIL_FETCH_EMAILS`). 없으면 Gmail 미연결 → 종료.
2. **알림 메일 검색** (최근 24~48h):
   - 링크드인: `from:user@example.com OR from:user@example.com newer_than:2d`
   - 원티드: `from:wanted.co.kr (추천 OR 공고 OR 포지션) newer_than:2d`
   - 사람인: `from:saramin.co.kr (맞춤 OR 공고) newer_than:2d`
   - 발신자가 다르면 제목 키워드로 폴백: `(job alert OR 새로운 공고 OR 맞춤 공고) newer_than:2d`
3. **본문에서 공고 추출** — 보드별 링크 패턴:
   - 링크드인: `linkedin.com/jobs/view/<id>` (트래킹 파라미터 `?...` 제거)
   - 원티드: `wanted.co.kr/wd/<id>`
   - 사람인: `saramin.co.kr/...rec_idx=<id>`
   - 링크 주변 텍스트에서 제목·회사를 함께 뽑는다. 리다이렉트 래퍼 URL이면 내부의 실제 URL 파라미터를 디코드한다.
4. **필터**: 시니어 신호(senior/lead/director/시니어/팀장 등) 제목은 제외. JD 확인 전이므로 판단 라벨은 비워둔다.
5. **파이프라인 병합**: **해당 보드의 상세 파일**(`automation/job-hunting/postings/ (공고 노트)`) 테이블에 공고링크로 dedup 후 추가 (허브에는 테이블 금지).
   행 형식: `| 회사 | 포지션 | <보드>(알림) | | 발견 | YYYY-MM-DD | <링크> |` (날짜 KST)
6. 이후 상위 후보는 `job-match`로 적합도 판정.

## Pitfalls
- 알림 메일에는 시니어·무관 공고도 섞인다 — 제목 필터를 거쳐도 **job-match JD 게이트가 최종 판정**.
- 같은 공고가 여러 메일에 반복 등장 → 반드시 링크 dedup.
- 메일 HTML의 트래킹 리다이렉트 링크는 원본 URL로 정규화해서 저장할 것.
- 크론에서 실행 시: Gmail 도구가 응답하지 않으면 재시도 말고 이번 회차는 건너뛴다.

## Verification
- [ ] Gmail 검색으로 최근 알림 메일이 1건 이상 잡힌다
- [ ] 본문에서 공고 링크·제목·회사가 추출된다
- [ ] postings/ 노트로 신규 공고가 생성되고 재실행 시 중복이 안 생긴다

## Google Alerts로 보드 밖 공고 수집 (일반 회사 채용페이지)
잡코·사람인·원티드에 없는 **회사 자체 채용페이지 공고**는 Google Alerts가 크롤링을 대신한다:
1. 사용자가 google.com/alerts 에서 알림 생성 (예: `"세일즈 오퍼레이션" 채용`, `"sales operations" 채용 -사람인 -잡코리아`, `BDR OR SDR 채용 신입`)
2. 알림 메일이 Gmail 도착 → 이 스킬이 기존 절차대로 파싱 → 링크·회사·포지션 추출해 postings/ 노트 생성(장부 dedup 동일)
3. Google Alerts 메일의 링크는 리다이렉트(`google.com/url?q=`) — 실제 URL을 풀어서 저장할 것.
