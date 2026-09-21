---
name: daily-journaling
description: "대화형 일일 기록/일기 작성. 위키 템플릿+캘린더 연동+한 번에 하나씩 Q&A."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [journal, diary, daily-note, wiki, calendar, conversational]
    category: note-taking
    related_skills: []
---

> **전제**: Composio MCP 연결 — Hermes 전용 아님. MCP를 지원하는 에이전트(Hermes·Codex·Claude Code 등) 어디서든 동일하게 동작한다.

# Daily Journaling

대화형 일기/저널 작성 워크플로우. 사용자의 위키 `journal/<YYYY-MM>/` 디렉토리에 일일 기록을 남기고, 캘린더 일정을 컨텍스트로 활용한다.

## When to Use — ❗ 반드시 로드할 것

사용자가 **일기, 하루 기록, 오늘 기록, 저널, journal, diary** 등 일일 기록 작성을 요청하면 **반드시 이 스킬을 먼저 로드**할 것. 스킬을 건너뛰고 바로 템플릿/위키부터 만지지 않는다.

- 사용자가 "일기 쓰자", "오늘 기록 남기자", "하루 정리" 등 일일 기록 작성을 요청할 때
- 사용자가 "일기 써볼까"처럼 애매하게 말해도 일기 작성 의도면 로드
- 크론 잡으로 매일 저녁 자동 일기 프롬프트를 보낼 때는 사용하지 않음 (이 스킬은 대화형 전용)

## Workflow

### 1. 오늘 일기 존재 여부 확인

```
search_files "YYYY-MM-DD" path="$WIKI/logs" target="files"
```

이미 있으면 내용 보여주고 추가할지 물어본다.

### 2. 템플릿 로드

```
# 데일리 노트 템플릿은 키트에 없다 — 아래 섹션 구성을 그대로 쓰면 된다(직접 템플릿을 만들었다면 그걸 읽어도 됨).
```

프론트매터: `title`, `created`, `type: log`, `tags: [일상]`, `mood: `

### 3. 캘린더에서 오늘 일정 가져오기

Composio MCP로 Google Calendar 조회:

- `COMPOSIO_SEARCH_TOOLS` → `GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS`
- timezone: `Asia/Seoul`
- time_min: `YYYY-MM-DDT00:00:00+09:00`
- time_max: `YYYY-MM-DDT23:59:59+09:00` (다음날 00:00:00+09:00)

### 4. 대화형 Q&A — ⚠️ 한 번에 하나씩 (강제 규칙)

**🔴 절대 여러 질문을 한 번에 묻지 않는다. 이건 위반 시 사용자가 명시적으로 싫어한다는 게 증명된 규칙이다.**

사용자가 "한꺼번에 묻지 말고 대화를 유도해달라고"라고 명시적으로 지시한 이력이 있음. 다음 같은 패턴은 **금지**:
- ❌ "오늘 기분 어땠어? 특별한 일 있었어? 내일 할 일은 뭐야?" (3개를 동시에)
- ❌ "오늘 어땠는지, 무슨 일 있었는지, 내일 계획은" (하나의 문장에 몰아넣어도 금지)

올바른 패턴:
- ✅ 질문 하나 던지고 → 사용자 응답 기다림 → 응답 확인 후 다음 질문
- ✅ "캘린더 일정 보여줬으면 → '오늘 기분 어땠어?' → 답변 듣고 반응 → '특별히 있었던 일 있어?' → ..."

자연스러운 대화 흐름으로 질문을 하나씩 던진다:

1. 캘린더 일정을 보여주며 시작
2. **"오늘 기분 어땠어?"** ← 첫 질문
3. 응답 후 → **"특별히 있었던 일 있어?"**
4. 응답 후 → **"내일 할 일은 뭐가 있어?"**
5. 필요하면 추가 질문 (스터디/모임에서 인상 깊었던 점 등)

각 질문은 이전 응답을 자연스럽게 받아서 이어간다. 응답을 받기 전에 다음 질문으로 넘어가지 않는다.

### 5. 노트 저장

채운 내용을 `journal/<YYYY-MM>/YYYY-MM-DD.md`에 `write_file`로 저장.

템플릿 구조:
```markdown
---
title: "YYYY-MM-DD 일일기록"
created: YYYY-MM-DD
type: log
tags: [일상]
mood: <사용자가 말한 기분 키워드>
---

# YYYY-MM-DD

## 오늘 할 일
- [x] ...

## 오늘 한 일
- ...

## 메모 / 생각
...

## 내일 할 일
- [ ] ...
```

### 6. log.md 기록

```
## [YYYY-MM-DD] create | 일일기록 작성
- journal/<YYYY-MM>/YYYY-MM-DD.md
```

## 캘린더 연동 패턴

일기 작성 중 캘린더 일정 조회나 추가가 필요할 때:

- 일정 조회: `GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS` (response_detail: "full")
- 일정 추가: `GOOGLECALENDAR_CREATE_EVENT` (start_datetime, timezone: Asia/Seoul, duration)
- 두 작업이 독립적이면 `COMPOSIO_MULTI_EXECUTE_TOOL`로 병렬 실행

## Pitfalls

- **한 번에 여러 질문 금지.** 가장 중요한 규칙.
- 템플릿의 `mood` 필드를 사용자가 말한 키워드로 채울 것 (비워두지 말 것)
- 캘린더 timezone 항상 `Asia/Seoul` (UTC+9)
- 일기 작성을 제안만 하고 저장하지 않고 끝내지 말 것 — 실제 파일 저장까지 완료
- Composio 세션 ID를 생성 후 재사용할 것 (`session: {generate_id: true}` → 이후 `session: {id: "..."}`)

## 사용자 주제 전환 처리

일기 작성 중 사용자가 자연스럽게 다른 주제로 전환할 수 있다 — 막지 말고 처리한 후 자연스럽게 복귀:

- **캘린더 조회/추가 요청** → Composio MCP로 처리 후 "일기에 반영했어"하고 다시 일기로 복귀
- **위치/거리/교통 문의** → `maps` 스킬 활용. 차량은 OSRM 가능, 대중교통은 OSRM 미지원이니 카카오맵/네이버지도 링크 제공
- **유튜브 영상 공유** → `youtube-content` 또는 `youtube-full` 스킬로 처리
- **면접/약속 준비물 문의** → 캘린더 일정 정보 바탕으로 체크리스트 제안

핵심: 전환된 주제를 처리한 후 자연스럽게 일기 주제로 복귀. 억지로 끌고 오지 말 것.

## Maps 연동 패턴

일기 중 사용자가 장소/거리/교통을 물으면:

```bash
# 차량 거리
# ⚠️ 아래 maps 스킬은 이 키트에 포함되지 않는다(외부/선택 스킬). 없으면 위치 관련 단계는 건너뛴다.
MAPS=$HERMES_DATA/skills/productivity/maps/scripts/maps_client.py   # optional — not shipped with this kit
python3 $MAPS distance "출발지" --to "도착지" --mode driving
python3 $MAPS search "장소명"  # 좌표/주소 확인

# 대중교통 (OSRM 미지원) → 링크 제공
# 카카오맵: https://map.kakao.com/?sName=출발&sX=경도&sY=위도&eName=도착&eX=경도&eY=위도
# 네이버 지도: https://map.naver.com/p/directions/출발경도,출발위도,,출발/도착경도,도착위도,,도착/transit
```

## 참고 파일
- 템플릿 파일은 없다. 섹션 구성(할 일·한 일·생각·내일)은 이 스킬 문서를 따른다.
- 저장 위치: `$HERMES_DATA/wiki/journal/<YYYY-MM>/` (기본 /opt/data)
- 캘린더 패턴: `references/calendar-patterns.md`
- 기업 리서치 (면접/미팅 준비): `references/company-research.md`