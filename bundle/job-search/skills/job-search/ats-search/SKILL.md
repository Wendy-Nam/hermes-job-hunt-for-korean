---
name: ats-search
description: "Use when the user wants to find jobs at foreign companies in Korea (국내 외국계) — they post on Western ATS (Greenhouse/Lever/Ashby), often more complete than LinkedIn. Filters Korea + keyword, gives direct apply URLs. 외국계 ATS 채용 검색."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, ats, greenhouse, 외국계, foreign, job]
    related_skills: [linkedin-search, job-match, wanted-search]
---

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부[발견/관심/준비/지원준비/지원/서류합격/면접/최종/오퍼/탈락/보류/마감]·게시일·링크·추가링크1~2·메모·우선도, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# 외국계 ATS 채용 검색 (ats-search)

국내 외국계는 채용을 **Greenhouse/Lever/Ashby**(서구 ATS)에 올린다. 링크드인은 그 일부만 미러링 →
**ATS 직접 조회가 더 완전 + 직접 지원 URL**. 공개 JSON API(인증 불필요). 영문 JD → 영문 이력서(en-*)로 지원.

## How
```bash
S=SKILL_DIR/scripts/ats_search.py
python3 $S --keyword sales --limit 40                 # 전체 대상사 Korea+키워드
python3 $S --keyword "business development" --json
python3 $S --company coupang --keyword 영업 --all-locations
# (수집은 job-collect.py 자동. 수동 --merge 폐기 — 공고는 노트라 표 병합 불가)
```
기본 **Korea 지역 필터**(--all-locations로 해제). 키워드는 직함 매칭.

## 대상 회사 관리 ★
이 스킬 폴더의 `data/ats-targets.json` — `{company, ats, token}` 목록. 사용자가 확장.
- **토큰 찾는 법**: 외국계 공고가 `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`, `jobs.ashbyhq.com/<token>` 로 링크아웃되면 그 `<token>`을 추가.
- 링크드인에서 외국계 발견 → "회사 사이트에서 지원" 링크의 도메인 확인 → 해당 ATS/토큰 등록.
- 현재 시드(검증됨): 쿠팡·Databricks·Datadog·Anthropic·MongoDB·Airbnb (모두 greenhouse).

## 파이프라인 연동
`--merge`로 postings/ 노트로 생성(링크 dedup, 보드=ats). 이후 `job-match`(영문 JD → en-sales/en-ops).

## Pitfalls
- 회사가 ATS/토큰을 바꾸면 조회 실패(스크립트가 스킵+경고). ats-targets.json 갱신.
- Korea 필터는 location 문자열 기반 — remote/APAC 공고는 --all-locations로.

## Verification
- [ ] `--keyword sales`가 외국계 Korea 공고 반환
- [ ] `--merge` 재실행 시 중복 안 생김
