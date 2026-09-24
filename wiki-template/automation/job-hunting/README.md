---
title: 구직 허브
type: index
lifecycle_status: active
private: true
tags: [구직, 허브]
---

# 구직 허브

구직 자료의 진입점이다. 공고 원문 1건은 `postings/` 노트 1개로 보존하며, 지원 상태와 세부 상태는 각 공고 노트의 기존 필드를 따른다.

## 바로가기
- [[strategy]] — 구직 전략·포지셔닝·소싱 ICP
- [[Rules]] — 필터·판정·상태 규칙의 단일 원본(SoT)
- [[pipeline]] — 수집부터 완료까지의 파이프라인
- [[postings/index]] — 채용공고 원문 모음
- [[sourcing/index]] — 소싱 실행 기록
- [[research/index]] — 회사·직무 리서치
- [[interview-reviews/index]] — 면접 준비·복기·전사
- [[Dashboard.base]] — TaskNotes 칸반·테이블·카드 대시보드

## 대시보드

`Dashboard.base`는 **Dataview**, **Tasks**, **TaskNotes** 플러그인이 설치된 Obsidian에서 연다. `공고` 또는 `posting` 태그가 있는 노트를 대상으로 하며, `우선도` 내림차순·`상태`/`세부` 기준으로 운영한다. 공고 원문은 `postings/`에 보존하고, 상태만 갱신한다.

## 상태 구분
- `상태`: `open`, `waiting`, `in-progress`, `done`
- `세부`: `발견`, `지원준비`, `지원`, `면접`, `과제`, `서류합격`, `오퍼`, `탈락`, `보류`, `마감` 등
- `우선도`: 자동 계산 필드. 손으로 임의 변경하지 않는다.

## 보존 원칙
- 공고 1건 = 노트 1개. 원문은 요약하거나 삭제하지 않는다.
- 변경은 frontmatter의 상태·판정 필드에 한정한다.

## 면접 준비 템플릿
- [[research/면접-리서치-템플릿]] — 면접 직전 15~30분 사전 리서치 양식.
- [[research/index]] — 리서치 목록과 사용법.
- [[interview-reviews/index]] — 회사별 `prep/research/review/transcript` 면접 패키지 구조.
