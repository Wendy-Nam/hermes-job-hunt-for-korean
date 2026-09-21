---
name: linkedin-search
description: "Use when the user wants LinkedIn job postings in Korea with experience/date/remote filters (신입·인턴·경력, 최근 1주 등). Returns links; can save to the Obsidian job-search pipeline table and track application status. 링크드인 채용 검색."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, 링크드인, linkedin, 인턴, 신입, job]
    related_skills: [wanted-search, daily-journaling]
---

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부[발견/관심/준비/지원준비/지원/서류합격/면접/최종/오퍼/탈락/보류/마감]·게시일·링크·추가링크1~2·메모·우선도, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# 링크드인 채용공고 검색 (linkedin-search)

로그인 불필요 게스트 엔드포인트로 한국 채용공고를 **경력/기간/근무형태 필터**와 함께 조회하고,
옵시디언 위키 postings/ 공고 노트에 저장·지원상태를 추적한다.

## When to use
- "링크드인에서 신입 데이터 공고 찾아줘", "인턴 마케팅 공고", "최근 1주 백엔드 채용" 등.
- 구직 파이프라인([[job-search]])에 공고를 수집·아카이빙하고 지원여부를 관리할 때.

## How — 검색
```bash
S=SKILL_DIR/scripts/linkedin_jobs.py
python3 $S "데이터 엔지니어" --level 신입,인턴 --since 1주 --limit 25   # 목록+링크
python3 $S "백엔드" --level 신입 --remote --json                      # 원자료(스코어링용)
python3 $S "마케팅" --level 인턴 --table                              # 파이프라인 행 미리보기
```
필터: `--level 인턴,신입,주니어,미드` · `--since 24h|1주|1달` · `--type 정규,계약,파트,인턴,임시` · `--remote` · `--location "South Korea"`

## How — 옵시디언 저장 (지원여부 관리) ★
```bash
python3 $S "데이터 분석" --level 신입 --since 1주 --limit 30 \
# (수집은 job-collect.py 자동. 수동 --merge 폐기 — 공고는 노트라 표 병합 불가)
```
- `--merge`: postings/ 공고 노트에 **신규 공고만**(공고링크로 dedup) 행 추가. 상태=`발견`, 갱신일=오늘(KST).
- 이후 지원 진행에 따라 Hermes가 해당 행의 **상태 칼럼**을 갱신:
  `발견 → 관심 → 지원준비 → 지원완료 → 서류통과 → 면접 → 최종 → 결과(합격/불합격/보류)`
- 회사 상세·지원서 초안은 `(공고 노트에 회사정보)<회사>.md`(템플릿 `templates/company.md`).

## 지원상태 갱신 예
사용자가 "쿠팡 데이터 공고 지원했어" 라고 하면 → `automation/job-hunting/postings/ (공고 노트)`에서 해당 링크 행을 찾아
상태를 `지원완료`, 갱신일을 오늘(KST)로 수정한다.

## Pitfalls
- **키워드 관련도 느슨**: 링크드인 게스트 검색은 매칭이 헐렁 → 구체 키워드 권장, 결과 확인 후 무관 공고 제외.
- **레이트리밋**: 스크립트에 요청 간 1.2s 딜레이 내장. 그래도 짧은 시간 반복 대량조회 금지(IP 차단 위험).
- **중복**: 스크립트가 링크로 dedup하지만, 같은 공고가 재게시(다른 id)로 또 뜰 수 있음.
- 약관상 스크래핑은 비권장 영역 — 개인·소량 조회로만.

## Verification
- [ ] `python3 SKILL_DIR/scripts/linkedin_jobs.py "테스트" --level 신입 --limit 3` 공고+링크 반환
- [ ] `--merge`로 postings/ 노트가 생성되고 재실행 시 중복 안 생김
