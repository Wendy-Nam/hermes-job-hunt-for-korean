---
title: Wiki Schema
created: 2026-07-06
updated: 2026-07-13
type: concept
tags: [reference]
---

# Wiki Schema

## Domain
사용자의 개인 세컨브레인. 일상 관리 + 구직/업무 자동화의 지식 베이스.
"나에 대한 것"(정체성·선호·목표·건강·재정·관계)과 진행 중인 업무·인물·지식을 한곳에 축적한다.
구조 = PARA(Projects/Areas/Resources/Archive) + Karpathy LLM Wiki. (2026-07-13 시맨틱 스키마로 재구성)

## 폴더 구조
```
wiki/
├── SCHEMA.md      # 이 파일 — 규칙과 taxonomy
├── index.md       # 전체 페이지 카탈로그
├── log.md         # 시간순 변경 로그(append-only)
├── profile/       # 나에 대한 핵심 (profile/<본인>.md — 정체성·선호·목표)
├── automation/job-hunting/        # 구직 자동화 모듈 (Rules.md=판정 규칙, postings/=공고 노트 — 파이프라인이 참조하는 구조 계약)
├── projects/      # 마감 있는 태스크 (interview/·life/·루트, TaskNotes 연동, 완료 시 _archive/)
├── areas/         # 지속 관리 — 영역당 페이지 1개 (health·mood·finance·relationships·home·hobby·career·learning)
├── people/        # 인물 페이지
├── concepts/      # 개념·지식 (resource)
├── journal/          # 데일리 저널 (YYYY-MM/YYYY-MM-DD.md 월버킷)
├── raw/           # 불변 원본 자료 통합 (attachments·transcripts·articles·clippings) — 바이너리든 텍스트든 원본은 전부 여기
├── hermes/        # Hermes 운영 (hermes.md·ops·skills-reference·runbooks.md·decisions.md)
└── _archive/      # 지나간 노트 (완료·폐기·대체) — 원본 자료가 아니라 '처리 끝난 노트'만
```
**Projects vs Areas**: projects는 끝이 있는 일(마감/목표, 완료 시 `_archive/`), areas는 계속 관리하는 영역(끝 없음).
**루틴/자동화 SOP**: 별도 routines/ 없음 — `hermes/runbooks.md`가 그 역할(200줄 초과 시 분할).
**메모리 계층**: `USER.md`(에이전트 자동 학습 요약, 매 턴 로드) ↔ `profile/<본인>.md`(사람이 큐레이팅한 상세) — 보완 관계.

## Conventions
- 모든 페이지는 YAML frontmatter로 시작, 수정 시 `updated` 갱신
- 페이지 간 연결은 `[[wikilinks]]`, 새 페이지는 `index.md`에 등록, 작업은 `log.md`에 append
- 구직 공고 노트의 상태 갱신은 반드시 `bin/note-set-field.py`(frontmatter 통째 재작성 금지)

## Frontmatter
```yaml
---
title: 페이지 제목
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: profile | project | area | entity | concept | journal
status: active | paused | done | someday   # projects에 유용
tags: [아래 taxonomy에서]
---
```

## Tag Taxonomy (여기 목록만 사용 — 난립 방지, 새 태그는 여기 먼저 추가)
- 삶의 영역: health, finance, career, learning, relationships, home, hobby
- 업무: project, task, automation, sop, tool, decision, 구직, 공고
- 사람/조직: person, org
- 지식: concept, reference, howto
- 메타: goal, habit, preference, journal, review, contested

## Page Thresholds
- 생성: 대상이 2회+ 언급되거나 한 소스에서 중심적일 때. 지나가는 언급은 생성 금지
- 추가: 기존 페이지가 있으면 병합(중복 페이지 금지 — sprawl 방지)
- 분할: ~200줄 초과 시. 아카이브: 대체된 페이지는 `_archive/` 이동 + index에서 제거

## Update Policy (모순 처리)
1. 최신 소스가 대체하는 것이 기본. 2. 진짜 모순이면 양쪽을 날짜·출처와 함께 기록, `contested: true` 표시.

## 프라이버시
비밀번호·API키·계좌·주민번호 평문 저장 금지. 이력서 등 개인정보 파일은 `private: true`.

## 자동화 계약 (깨지 말 것)
- `automation/job-hunting/Rules.md`(판정 SoT)·`search-profile.yaml`(수집 SoT)·`.job-*.json`(장부) 구조 불변
- 데일리 노트 경로 `journal/<YYYY-MM>/` — 크론(선톡·자동로깅)이 참조
- `hermes/skills-reference.md` — wiki-skills-sync.py가 재생성
