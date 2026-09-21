---
title: 구직 (Jobs)
type: index
private: true
---

# 🎯 구직 (Jobs)

**공고 1건 = `postings/` 노트 1개.** frontmatter: 회사·포지션·보드·적합도·**상태**(대분류)·**세부**(소분류)·게시일·링크·추가링크1~2·메모.
- 상태(대분류): 예정 / 지원완료 / 진행중 / 완료 · 세부(소분류): 발견·관심·준비 / 지원 / 서류합격·면접·최종·오퍼 / 탈락·보류·미스핏

## 뷰
- **Dashboard.base** (Obsidian Bases) — 표·카드. 카드 클릭 = 상세노트.
- **TaskNotes 칸반** — 공고 노트에 `task` 태그 + `상태` 필드가 있어, TaskNotes에서 **tag `공고`로 필터 + group by 상태** → 예정/지원완료/진행중/완료 4컬럼, 드래그로 상태변경(= `상태` frontmatter 갱신).

## 구조
- `postings/` — 공고 노트. 같은 공고가 여러 URL이면 새 노트 대신 `추가링크1~2`에 병합(중복 방지).

## 자동화 (Hermes가 대신)
- 수집: `scripts/job-collect.py`(하루 2회, URL장부+회사·포지션 dedup, LLM 없음).
- 판정: 구직 트리아지 크론(12시간마다, 신규에 적합도).
- 정리: 주1회 방치 발견노트 삭제(장부 유지 → 재등장 안 함).
- 상태변경·로깅: 대화/Gmail 기반으로 Hermes가 `상태`·`세부` frontmatter 수정.
