---
name: apify-search
description: "Use to scrape job boards that the free scrapers can't reach (리멤버/Remember) or as a robust fallback when wanted/linkedin/saramin/jobkorea scraping breaks or is blocked. Runs Apify actors via the Composio MCP. Apify 액터로 공고 수집."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, apify, composio, 리멤버, remember, job]
    related_skills: [kr-search, linkedin-search, wanted-search, job-match]
---

> **전제**: Composio MCP 연결 — Hermes 전용 아님. MCP를 지원하는 에이전트(Hermes·Codex·Claude Code 등) 어디서든 동일하게 동작한다.

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부·링크·추가링크1~2·메모, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# Apify 경유 공고 수집 (apify-search)

무료 스크래퍼가 못 뚫는 보드(리멤버 등)나, 기존 스크래퍼가 깨졌을 때의 폴백.
**Apify 액터를 Composio MCP로 실행**한다. (Apify는 Composio에 연결돼 있음)

## 언제 쓰나 (하이브리드 원칙)
- **1차는 항상 무료 스크래퍼**: `wanted-search` · `linkedin-search` · `kr-search`.
- **이 스킬은**:
  - 🎯 **리멤버(career.rememberapp.co.kr)** — SPA+로그인이라 무료로 불가.
  - 🛟 무료 스크래퍼가 구조변경/차단으로 실패했을 때 폴백.
  - 📈 대량·고신뢰 수집이 필요할 때(프록시·헤드리스가 필요한 경우).
- Apify는 **크레딧이 든다** → 무료로 되는 건 무료로. 남발 금지.

## 워크플로 (Composio MCP 툴 사용)
1. **액터 검색**: `COMPOSIO_SEARCH_TOOLS` 로 목적에 맞는 Apify 액터를 찾는다.
   - 리멤버/일반 사이트: 범용 웹스크래퍼 계열(예: apify web-scraper / puppeteer-scraper) 또는 링크드인/잡보드 전용 액터.
   - 검색어 예: "apify linkedin jobs", "apify web scraper", "apify job board".
2. **스키마 확인**: `COMPOSIO_GET_TOOL_SCHEMAS` 로 입력 파라미터 확인(startUrls, keyword, maxItems 등).
3. **실행**: `COMPOSIO_MULTI_EXECUTE_TOOL` 로 액터 실행.
   - 리멤버 타깃 URL 예: `https://career.rememberapp.co.kr` 검색 결과 페이지 / 로그인 필요 시 액터의 세션·쿠키 입력 파라미터 사용.
4. **결과 파싱 → 파이프라인 병합**: 액터 dataset(제목·회사·링크)을 표준 행으로 변환해
   해당 보드의 상세 파일(`automation/job-hunting/postings/ (공고 노트)`, 리멤버는 신설) 테이블에 추가(공고링크로 dedup, 상태=발견, 갱신일=KST). 허브에는 테이블 금지.
   행 형식: `| 회사 | 포지션 | remember | | 발견 | YYYY-MM-DD | 링크 |`
5. 이후 `job-match`로 적합도 평가.

## Pitfalls
- 어떤 액터가 연결/가용한지는 **런타임에 `COMPOSIO_SEARCH_TOOLS`로 확인** — 하드코딩하지 말 것.
- 리멤버 로그인 필요 시: 세션 쿠키를 받는 액터를 쓰거나, 사용자에게 세션 제공/직접 조회를 요청.
- 크레딧·실행시간 고려. 소량으로.

## Verification
- [ ] `COMPOSIO_SEARCH_TOOLS`가 실행 가능한 Apify 액터를 반환
- [ ] 실행 결과가 postings/ 노트로 dedup 생성됨
