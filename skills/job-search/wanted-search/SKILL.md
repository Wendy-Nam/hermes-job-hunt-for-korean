---
name: wanted-search
description: "Use when the user wants to search Korean job postings on 원티드/Wanted — by keyword (role, stack, company type). Returns title, company, location, reward, and link. 구직·채용·공고 검색."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, 원티드, wanted, job]
    related_skills: []
---

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부[발견/관심/준비/지원준비/지원/서류합격/면접/최종/오퍼/탈락/보류/마감]·게시일·링크·추가링크1~2·메모·우선도, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# 원티드 채용공고 검색 (wanted-search)

## When to use
- 사용자가 "원티드에서 ~ 공고 찾아줘", "백엔드 채용 뭐 있어", "데이터 엔지니어 구인" 등 한국 채용공고 검색을 요청할 때.
- 구직 파이프라인([[job-search]] 위키)에 공고를 수집·아카이빙할 때의 1차 소스.

## How
공개 API(인증 불필요)로 조회한다. `SKILL_DIR/scripts/wanted_search.py`:

```bash
# 기본 (마크다운 목록)
uv run python3 SKILL_DIR/scripts/wanted_search.py "백엔드" --limit 20

# 원자료(JSON) — 파이프라인/스코어링용
uv run python3 SKILL_DIR/scripts/wanted_search.py "데이터 엔지니어" --limit 30 --json
```

출력 필드: 제목 · 회사 · 지역 · 보상 · 링크(`wanted.co.kr/wd/<id>`).

## 파이프라인 연동
1. 검색 → 관심 공고를 위키 `automation/job-hunting/postings/ (공고 노트)` 보드 테이블에 추가(상태=발견). 허브(job-search.md)에는 테이블 금지.
2. 회사별 상세는 `(공고 노트에 회사정보)<회사>.md`(템플릿 `templates/company.md`).
3. 적합도 매칭은 이력서 등록 후 career-ops 6블록 기준(추후 P4).

## Pitfalls
- 원티드가 응답 구조를 바꾸면 `positions` 키가 달라질 수 있음 → `--json`으로 구조 확인 후 `scripts/wanted_search.py`의 `norm()` 조정.
- 대량 조회 시 limit를 키우되 예의상 과도한 반복 호출 금지.

## Verification
- [ ] `uv run python3 SKILL_DIR/scripts/wanted_search.py "테스트키워드" --limit 3` 가 공고 목록 반환
- [ ] 링크가 실제 공고로 연결됨
