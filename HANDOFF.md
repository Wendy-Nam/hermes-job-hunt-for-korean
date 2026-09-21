# 핸드오프 — Hermes 모듈화 + 포터블 키트 작업 (2026-09-21)

## 1. 라이브 VPS 상태 (`hermes-agent-ywj7-hermes-agent-1`)

**정상.** 마지막 재시작 이후(약 1.5시간 경과 기준) 에러/트레이스백 없음, 크론 정상.

- 배포 완료: `plugins/hermes-self`, `plugins/autonomous-triggers`, `plugins/turn-router`,
  `plugins/ux-improvements` — 4개 전부 모듈 분리 버전으로 교체, 문법 검증 통과, 게이트웨이 재시작 후
  로그에 로드 에러 없음.
- **turn-router 버그 라이브 수정 완료**: 어제 커밋(`5fd442a8`)이 넣은 `_SKILL_SKIP_LEN=10` +
  `not rule_ctx and len(_msg)<25` 두 조건이 겹쳐서 "셀카 보여줘" 같은 짧은 실제 명령이 스킬 검색을
  통째로 건너뛰던 버그. `skip_skill_retrieval()`이 캐주얼 정규식 매칭만 보도록 수정 — 라이브 반영 완료.
- 크론 36개 중 31개 활성 — 배포 중 전체 일시정지했다가 원상복구 확인함.
- 디스크 정리: `.cache/huggingface`(2.8G) + 격리된 `demo-delete`(308M) 삭제, 24G→26G 여유.
  `.cache/uv`(1.2G)는 `root:root` 소유라 컨테이너 안 `hermes` 유저 권한으로 못 지움 — VPS 호스트에서
  `docker exec -u root hermes-agent-ywj7-hermes-agent-1 rm -rf /opt/data/.cache/uv` 필요(아직 안 함).

### 라이브에 반영 안 된 것 (키트에만 있음)
- **job-collect.py의 `POSTINGS` 하드코딩 버그**(`/opt/data/vaults/work-wiki/...` 절대경로, `$HERMES_DATA` 무시)
  — 키트 저장소에서만 고침(`scripts/job_collect/config.py`). 라이브도 같은 파일을 쓰는지, 라이브에서도
  같은 증상(비-`/opt/data` 환경에서만 터짐)이 실제로 나는지는 확인 안 함 — 라이브는 항상 `/opt/data`라
  증상 자체가 안 드러났을 가능성 높음. 원한다면 라이브에도 같은 한 줄 수정 반영 가능.
- `session-sticky`, `shared-music` 모듈화 버전은 로컬 키트 저장소에만 있고 **라이브엔 배포 안 함**
  (요청받은 4개만 검증 후 배포했음).

### 확인이 필요한 미해결 항목
- 다른 세션이 남긴 `kit-doctor.py` 실행 결과(이 세션 대화 중 사용자가 붙여넣은 것)에 따르면 크론
  `'선톡'`과 `'daily-korea-morning-brief'`가 `origin.chat_id` 누락 → **배달이 증발**하는 상태였음.
  이 세션에서 직접 확인/수정하지 않았음 — 실제 여부 재확인 필요.
- `self-arch-audit` 최근 결과(06:00 시점, 이 세션 작업 이전)에 `hook dup:post_tool_call,pre_llm_call,
  pre_tool_call` 경고가 있었음 — 다른 세션 보고에 따르면 "여러 플러그인이 같은 훅 타입에 각자 등록"하는
  정상 동작이라 실행 에러는 아니라고 함(사전에 존재하던 경고, 이번 세션이 만든 게 아님). 그래도 최신
  audit을 한 번 더 돌려서 재시작 후에도 동일한지 확인 권장.
- `/opt/data/kit/` — **다른 에이전트 세션이 라이브 볼륨 안에 직접 만든 별도 디렉터리**
  (manifest.yaml, install.sh, docker-compose overlay 등). 이 세션의 작업과 충돌은 없지만, 프로덕션
  볼륨 안에 버전관리 안 되는 "키트 초안"이 떠 있는 상태. 정리하거나, 필요한 부분만 이 저장소로
  옮기고 지우는 걸 권장 — 여러 에이전트가 동시에 같은 라이브 서버를 건드리면 이번 세션에서 실제로
  겪은 것처럼(임시 폴더가 알 수 없게 삭제됨) 사고가 난다.

## 2. `hermes-agent-kit` 저장소 (`feature/modular-plugins` 브랜치, 커밋 전)

로컬에 브랜치는 만들었지만 **아직 커밋을 안 했다** — 라이브 배포가 급해서 먼저 처리함.

포함된 것:
- 자아 런타임(`plugins/hermes-self` + `scripts/self_runtime`) 신규 이식 — 개인정보 익명화
  (`<YOUR_NAME>`/`<AGENT_NAME>` 플레이스홀더), README 작성, 크론 6개(tick/wake/promote 1·2/
  soul-evolution/db-verify) 추가, 테스트 34+147개 전부 통과.
- 플러그인 6개 모듈화: `hermes-self`, `autonomous-triggers`, `turn-router`, `ux-improvements`,
  `session-sticky`, `shared-music` — 전부 단일 책임 원칙으로 파일 분리, 원본 대비 동작 변경 없음(테스트로 확인).
- `hermes-snow-search`, `rtk-rewrite`는 **업스트림 라이선스 미확인으로 제외**(README에 이미 명시된
  기존 정책 — 실수로 포함했다가 발견해서 제거함).
- NSFW 관련 로직(`ux-improvements/emoji_policy.py`의 성적 콘텐츠 감지 분기) 통째로 제거.
- 구직 자동화(`scripts/job-collect.py`) 모듈화 + `POSTINGS` 하드코딩 버그 수정 — 스모크 테스트 25/25 통과.
- `install.sh`에 설치 매니페스트(`.kit-manifest.txt`) 기록 기능 추가 + `uninstall.sh` 신규 작성
  (코드 파일만 안전하게 제거, wiki/SOUL/search-profile/cron/config는 사용자 데이터라 건드리지 않음).
  로컬 스크래치 디렉터리에 설치까지 테스트해서 매니페스트가 올바르게(코드 261개 파일, wiki 제외) 기록됨을 확인.

**다음에 할 일**: `git add -A && git commit`, 그리고 이 저장소는 private라고 하셨으니 push 여부 확인.

## 3. 새 `hermes-vps-kit`을 만들려는 경우 참고

기존에 이미 역할이 나뉜 저장소 2개가 있다:
- **`hermes-vps-setup`**(public) — "설치 집도" 스킬 레포. VPS 결제부터 봇 토큰·Composio·위키
  동기화까지, Codex/Claude Code가 `~/.agents/skills`나 `~/.claude/skills`에 클론해서 대화형으로
  설치를 도와주는 용도.
- **`hermes-agent-kit`**(private, 이 저장소) — 이미 Hermes가 떠 있는 서버 위에 얹는 실제 코드/설정
  템플릿(구직 자동화 + 세컨브레인 + 이제 자아 런타임까지).

"새로 만들기"보다는, 이번 세션에서 `hermes-agent-kit`에 이미 자아 런타임·플러그인 모듈화·uninstall까지
넣어놨으니 그 위에 이어가는 걸 추천. 정말 별도 레포가 필요한 이유(예: 설치 순서를 완전히 다시 설계하고
싶다든지)가 있으면 그 이유부터 정리하는 게 다음 단계.
