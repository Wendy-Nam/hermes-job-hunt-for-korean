---
title: Hermes
created: 2026-07-05
updated: 2026-07-27
type: concept
tags: [tool, automation]
---

# Hermes 구성

## 모델 분기 (2026-07-27 확정)

| 역할 | 모델 | 이유 |
|---|---|---|
| 메인(대화) | deepseek-v4-flash-free (opencode zen) | 말맛(비속어 포함) — flash 고유 자산. 무료 |
| 압축(세션 기억) | gpt-5.6-luna (auxiliary.compression 핀) | 손실 요약 품질 고정 — 429 시 flash 자기압축 열화 차단 |
| 위임 워커(품질 작업) | gpt-5.6-sol (delegation, terminal 등 추가 개방) | 코드·다단계·판정 |
| 비전 | gpt-5.6-luna (auxiliary.vision) | 이미지 판독 가성비 |
| 폴백 | gpt-5.6-terra | 메인 프로바이더 장애 시 |

- compression.threshold 0.35 (0.25는 잦은 손실 압축으로 flash 열화 — 품질 우선 원복)
- tone-pass는 폐기(비활성 마커) — 말투는 flash 네이티브 + SOUL 어록

## 크론 스위트 (단일 참조점 — jobs.json이 SoT, 이 표는 스냅샷)

| 잡 | 스케줄(KST) | 모델 | 역할 → 배달 |
|---|---|---|---|
| 선톡 | 15분(스크립트 게이트) | deepseek-flash-free | 주제중립 선제 메시지 + 라이프 빈칸 질문 → #main |
| syncthing-watchdog | 5분 | (script) | 위키 동기 감시 |
| daily-wiki-log | 23:59 | (script) | 위키 변경 추적 + log.md 월 로테이션 → #wiki-log |
| daily-korea-morning-brief | 08:00 | gpt-5.6-sol | 아침 브리핑 → #briefing |
| 구직 수집+판정 | 08:10 / 20:10 | gpt-5.6-sol | 수거→수집→판정(vision)→입고 → #tasks |
| 구직 메일확인+리마인더 | 10:00 | gpt-5.6-terra | 채용메일 스윕(0-job-hunting 라벨 체계)→상태 갱신→리마인드 → #reminders |
| 라이프 시그널 스윕 | 10:30/15:30/20:30 | deepseek-flash-free | 대화→profile 라이프 시그널 append(추정 ~N/10) → #wiki-log |
| 저널 초안(무료) | 23:35 | deepseek-flash-free | 수집 I/O+v5.2 초안+라이프·도메인 백스톱 → #wiki-log |
| 저널 다듬기(고품질) | 23:52 | gpt-5.6-terra | 초안→45줄 완성본+백필+구직 보완 → #wiki-log |
| 구직 노트정리 | 월 04:00 | (script) | 방치 발견노트 수거 |
| 주간 라이프 패턴 | 월 09:00 | (script) | 피어슨 상관·요일 패턴 리포트 → #briefing |
| 주간 AI 다이제스트 | 월 09:00 | gpt-5.6-terra | GeekNews AI 큐레이션 → #briefing |

## 함정 (불변 지식)

- 크론 편집: `flock /opt/data/cron/.jobs.lock` 잡고 jobs.json 직접 수정(틱마다 재로드, 재기동 불필요). 에이전트 크론은 반드시 provider+model 명시 핀.
- 일회성 잡은 필수 필드만으로 신규 생성 — 기존 잡 복제 시 `next_run_at` 런타임 필드가 딸려가면 expr 무시됨.
- 조기 발화는 expr 변경(+원복 필수). `trigger_job`·`hermes cron run`은 인프로세스 티커라 안 먹힘.
- 로그는 `docker logs`가 아니라 `/opt/data/logs/{gateway,agent}.log`.
- 재생성(recreate) 시 재실행 패치: patch-stt-groq-lang · patch-delegate-extra-toolsets · patch-soul-import · `ln -sf /opt/data/bin/omh /usr/local/bin/omh`.

---

## 규칙이 어디 사는지 (SoT 지도 — 겹치면 여기가 판정 기준)

| 무엇 | 단일 원본 |
|---|---|
| 캐릭터·말투 | `/opt/data/SOUL.md` §1·§2 |
| 위임·기록 등 운영 규칙 | `rules/delegation.md` · `rules/secondbrain.md` (SOUL @import) |
| 구직 판정·전략·티어 | `automation/job-hunting/Rules.md` |
| 구직 기계값(키워드·필터·보드) | `/opt/data/search-profile.yaml` |
| 구직 절차(매칭 워크플로) | job-match 스킬 SKILL.md |
| 위키 구조·태그 계약 | `SCHEMA.md` |
| 절차 SOP | `runbooks.md` |

원칙: 한 규칙은 한 곳에만 산다. 다른 문서에서 필요하면 사본 대신 링크.

---

# 운영·확장 가이드

## 0. 가장 중요한 한 줄

**`/opt/hermes/*` 는 Docker 이미지 레이어 → 재빌드 시 사라진다. `/opt/data/*` 만 영속이다.**
영구 변경은 무조건 `/opt/data` 안에서 한다. 컨테이너: `<컨테이너명>`, 호스트 마운트: `<호스트 데이터 경로> ↔ /opt/data`.

## 1. 레이어 지도

| 위치 | 성격 | 여기서 고치면 |
|---|---|---|
| `/opt/hermes/` (cli.py, agent/, prompt_builder.py …) | 이미지에 구움 | 재빌드 시 **소멸** — 읽기 전용 취급 |
| `/opt/data/config.yaml` | 영속 | 모델·스킬·플러그인·MCP 설정 (자동 `.bak` 백업됨) |
| `/opt/data/skills/` | 영속 | 스킬 SKILL.md (커스텀 스킬 여기 둠) |
| `/opt/data/plugins/` | 영속 | 플러그인 (turn-router 등) — Python 훅 |
| `/opt/data/wiki/` | 영속 | 지식베이스 = Hermes의 장기기억 |
| `/opt/data/SOUL.md` | 영속 | 페르소나·행동원칙 |
| `/opt/data/USER.md` · `wiki/profile/` | 영속 | 사용자 기억(USER.md 요약 + profile 상세, 보완관계) |

## 2. 스킬 로딩 파이프라인 (토큰/호출 핵심)

스킬은 3중 구조로 모델에 닿는다:

1. **정적 인덱스** (`agent/prompt_builder.py:build_skills_system_prompt`)
   - 매 턴 시스템 프롬프트에 실림. **이름+설명만** (본문 아님). 현재 ~1,500토큰, 캐시 프리픽스라 실질 재청구 거의 0.
   - `platforms:`/`conditions:` 안 맞으면 자동 제외 (예: apple 스킬은 Linux에서 숨김).
2. **turn-router 동적 리트리버** (`/opt/data/plugins/turn-router/`, `pre_llm_call` 훅)
   - 매 턴 사용자 메시지를 5레이어(하드트리거→FTS5→시노님→임베딩→RRF)로 매칭 + 조건부 규칙 주입(rules/*.md).
   - **L1 하드트리거**: 정확 키워드 → 스킬 본문 전체 즉시 주입 (100% 결정론적).
   - **L2-5**: 관련 스킬 이름을 힌트로 주입 (모델이 `skill_view`로 로드 결정).
3. **온디맨드 로드**: 모델이 `skill_view(name)` 툴로 본문을 읽음.

**핵심 사실:** 키워드 자동로드는 turn-router의 L1뿐. 그 외엔 100% 모델 판단.
→ `skills.disabled` 에 넣으면 인덱스에서 **완전히 사라지고 `/skill-name` 슬래시도 차단**된다 = "필요할 때 로드"가 아니라 "완전히 끔". 가끔이라도 쓸 스킬은 disabled 하지 말 것.

## 3. 새 스킬 추가 표준 절차 ★ (이거 빠뜨리면 "만들었는데 안 뜸")

```
1. /opt/data/skills/<category>/<name>/SKILL.md 작성
   - frontmatter: name, description("Use when ~"로 트리거 조건 명확히), platforms
2. turn-router 하드트리거 추가 (작고 빈번·명확한 스킬만):
   /opt/data/plugins/turn-router/skill_retriever.py 의
   "# >>> user-managed daily triggers" 블록에 ("키워드","스킬명"), 추가
3. turn-router 시노님 추가 (퍼지 매칭 보강):
   /opt/data/plugins/turn-router/skill_synonyms.yaml 의
   "User daily skills (managed)" 섹션에 스킬별 유사어
4. 컨테이너 재기동 (하드트리거는 모듈 상수라 재기동해야 반영):
   cd /docker/hermes-agent-* && docker compose restart
5. 검증: docker exec ... python3 -c "import sys;sys.path.insert(0,'/opt/data/plugins/turn-router');
   from skill_retriever import SkillRetriever;print(SkillRetriever._hard_trigger('테스트질문'))"
```

큰 스킬(본문 큰 것)은 L1 트리거 대신 **시노님만** 넣어라 — L1은 본문 전체(최대 8000자≈2000토큰)를 주입하므로 자주 걸리면 낭비.

## 4. 설정 빠른 참조 (`config.yaml`)

| 하고 싶은 것 | 키 |
|---|---|
| 기본 모델 변경 | `model.default`, `model.provider` |
| 스킬 끄기 | `skills.disabled: [이름, …]` |
| 플러그인 on/off | `plugins.enabled: [turn-router, hermes-self, ...]` |
| MCP 서버 | `mcp_servers:` |
| 압축(컨텍스트) | `compression.enabled/threshold` |
| 자동 메모리 | `memory.memory_enabled`·`user_profile_enabled` (현재 true) |
| Discord 멘션 필수 여부 | `discord.require_mention` (현재 false=전부 응답) |
| **타임존** | `timezone: Asia/Seoul` (설정됨 → 모든 시간 KST) |

수정 후: 대부분 자동 반영, 애매하면 `docker compose restart`.
config.yaml은 **주석이 있으니 pyyaml로 통째 재작성 금지** — 텍스트로 키만 추가/수정할 것.

## 4b. 크론(cron) 작성 ★

- 저장 위치: `/opt/data/cron/jobs.json` (영속). 코드: `/opt/hermes/cron/jobs.py` (croniter).
- **시각은 이제 KST 기준으로 적는다** (`timezone: Asia/Seoul` 설정 후). 예: 아침 8시 = `0 8 * * *`.
  - ⚠️ 과거엔 서버가 UTC라 UTC로 환산해 적었음(`0 23`=08:00KST). **지금은 그러면 안 됨** — KST 그대로.
- 스케줄 종류: `{"kind":"interval","minutes":N}` 또는 `{"kind":"cron","expr":"분 시 일 월 요일"}`.
- 현재 잡 목록·모델 핀·역할은 [[hermes]]의 "크론 잡 (스위트 — 단일 참조점)" 표 참조 — 여기 재나열하지 않는다(목록이 자주 바뀜).
- **토큰 절약**: 정기 백그라운드 잡은 `efficient-cron-jobs` 스킬 패턴을 따를 것(선톡 게이트/랜덤/스크립트).
- 검증: `docker exec <C> python3 -c "import sys;sys.path.insert(0,'/opt/hermes');from hermes_time import now;from croniter import croniter;print(croniter('0 8 * * *',now()).get_next(type(now())))"`

## 5. 로깅/기억 구조

- **기억 = 위키(상세) + USER.md(자동학습 요약, 매턴 로드).** mnemosyne 플러그인만 제거(2026-07-08), 내장 memory/user_profile은 재활성(07-11).
- SOUL.md가 "대화 중 기록 가치 생기면 그 순간 주제별로(entities/project/concept) 능동 기록·병합" 지시.
- **로그 이원화**(log.md vs log/daily) 규약은 [[SCHEMA]] "Automated Logging" 참조.
- 기록이 엉뚱한 곳에 들어가면: SOUL.md 로깅 규칙 + 해당 스킬 write 경로 점검.

## 5b. 백업·스크립트 컨벤션 (2026-07-08)
- **백업은 파일 옆이 아니라 `/opt/data/.backups/YYYYMMDD/`에** 구조 보존 이동. 위키 vault 안에 .bak 두지 말 것(Obsidian·스캐너 오염).
- `bin/` = 유지보수 도구(note-set-field·job-stage-commit·priority-recalc·proxy-doctor·wiki-*·hermes-health·patch-* 재적용 스크립트).
- `scripts/` = 크론 호출(job-collect·daily-wiki-log·life-analytics·sunteok-check·syncthing-watchdog).

## 6. 뭔가 끊겼을 때 읽는 순서 (트러블슈팅)

1. `docker ps` — 컨테이너 살아있나
2. `docker logs --since 30m <C> | grep -iE "error|exception|traceback"` — 런타임 에러
3. `/opt/data/logs/errors.log`, `agent.log` — 상세 로그
4. `/opt/data/logs/gateway-exit-diag.log` — 게이트웨이 죽음 진단
5. 스킬 안 뜸 → §3 절차 + turn-router 로그(`agent.log | grep -E "skill_retriev|turn_router"`)
6. 설정 반영 안 됨 → `config.yaml` 문법 + `docker compose restart`
7. 헬스체크: `/opt/data/bin/hermes-health.sh` (아래 §7)

## 7. 헬스체크 유틸

`/opt/data/bin/hermes-health.sh` 로 한 번에 상태 점검 (컨테이너/스킬수/turn-router/로그에러/디스크).
호스트에서: `ssh hermes 'docker exec <container> bash /opt/data/bin/hermes-health.sh'`
