---
name: kr-search
description: "Use when the user wants Korean job postings from 사람인(Saramin) or 잡코리아(JobKorea) by keyword. Returns title/company/link; can merge into the Obsidian job-search pipeline. 사람인·잡코리아 채용 검색."
version: 1.0.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립(맥 스모크 통과) — 서버 설치 관례(chown·크론)만 Linux
metadata:
  hermes:
    tags: [구직, 채용, 사람인, 잡코리아, saramin, jobkorea, job]
    related_skills: [wanted-search, linkedin-search, job-match]
---

> **현재 구조 (2026-07-08):** 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·적합도·상태[예정/지원완료/진행중/완료]·세부[발견/관심/준비/지원준비/지원/서류합격/면접/최종/오퍼/탈락/보류/마감]·게시일·링크·추가링크1~2·메모·우선도, tag `공고`+`task`). 수집은 `scripts/job-collect.py`(크론, URL장부+회사·포지션 dedup)가 자동. 이 스킬 스크립트는 조회용이자 job-collect가 재사용한다. **수동 `--merge`는 폐기**(공고는 표가 아니라 노트). 규칙 SoT: `automation/job-hunting/Rules.md`.


# 사람인·잡코리아 채용 검색 (kr-search)

## When to use
- "사람인에서 백엔드 찾아줘", "잡코리아 데이터 공고", "국내 채용 싹 긁어줘" 등.
- 구직 파이프라인([[job-search]])에 국내 공고 수집.

## How
```bash
S=SKILL_DIR/scripts/kr_job_search.py
python3 $S "백엔드" --board saramin --limit 20        # 사람인
python3 $S "데이터 분석" --board jobkorea --limit 15   # 잡코리아
python3 $S "PM" --board all --limit 30 --json         # 둘 다 (원자료)
# 위키 파이프라인에 병합 (링크 dedup, 상태=발견, KST)
# (수집은 job-collect.py 자동. 수동 --merge 폐기 — 공고는 노트라 표 병합 불가)
```

## 보드 특성
- **사람인**: 제목·회사명 모두 파싱됨. 안정적.
- **잡코리아**: React 앱이라 회사명은 제목의 `[회사명]`에서 폴백 추출(없으면 공란) — 상세는 링크 클릭. 제목·링크는 정확.

## 전체 보드 커버리지 (참고)
국내 구직 = **원티드(`wanted-search`) + 링크드인(`linkedin-search`) + 사람인·잡코리아(이 스킬)**.
리멤버는 SPA/로그인 필요라 현재 미지원.

## Pitfalls
- 웹 구조 변경 시 파싱 깨질 수 있음 → `--json`으로 확인 후 `scripts/kr_job_search.py` 조정.
- 예의상 짧은 시간 대량 반복 조회 금지(스크립트에 1.2s 딜레이 내장).

## Verification
- [ ] `--board saramin` 결과에 회사명 포함
- [ ] `--merge` 재실행 시 중복 안 생김
