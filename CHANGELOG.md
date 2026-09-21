# Changelog

유튜브·프록시(2026-09-12):
- **youtube-content 스킬 편입 + Gemini 경로** — `scripts/gemini_video.py`: 유튜브 URL을 Gemini `file_data`로 넘겨 서버사이드로 시청(요약·질문답·전사 대용). 클라우드 IP 차단과 무관, 무료 티어, stdlib만. `data/.env`의 `GEMINI_API_KEY`를 직접 읽어 재시작 불필요. 자막 API(`fetch_transcript.py`)는 데이터센터 IP(VPS·Webshare datacenter 전부)에서 차단됨을 실측 — residential 프록시가 있을 때만 2순위.
- **수집기 프록시 스코핑** — `scripts/job-collect.py`가 프록시를 `PROXY_BOARDS=("wanted",)`(+wanted.co.kr JD fetch)에만 주입, 나머지 보드·ATS는 직접 연결. 무료 프록시 대역폭 소진·수집 지연의 원인이 전 보드 경유였음.
- **proxy-doctor 생존검사 = 실제 타깃** — google 204 대신 원티드 API(limit=1, 브라우저 UA, 200만 인정)로 검사하고 후보 12개. 데이터센터 IP는 원티드 WAF를 10개 중 2개만 통과해서, 살아 있어도 403인 프록시를 고르던 문제 수정.

라이프 트래킹 + SOUL 모듈화(2026-07-26):
- **life-os 스킬 신설**(`skills/note-taking/life-os/`) — 기분·수면·식사·운동·스트레스·습관 시그널을 대화에서 주워 `profile/`(날짜 섹션 append)·`journal/`에 기록하고 패턴(3일 연속 저하·요일 패턴·스트릭·상관)을 감지. Lethe044/hermes-life-os(MIT) 플레이북을 위키 배선으로 각색 — append 전용, frontmatter 통째 재작성 금지, 날조 금지.
- **SOUL 모듈형 룰북(선택)** — `bin/patch-soul-import.py`: SOUL.md의 `@import <절대경로>` 줄을 매 턴 인라인 확장(멱등, HERMES_HOME 밖 거부, 40KB 상한, 파일 없으면 포인터로 fail-open). 이미지 레이어 패치라 컨테이너 재생성 시 재실행.
- **자동 로깅 크론 v2** — 수집을 "user+최종 응답만"→전체 대화(중간 문답 포함)로, 데일리 섹션에 느낌/반응·재밌는 얘기 추가, "하루의 색 보존"(실제 인용 1~3개, 결정·진행 편중 금지) 규정. 결정 위주로만 남고 대화의 재미가 소실되던 문제의 근본원인이 수집 쿼리였음.

## 0.1.0-beta (2026-07-16)
첫 공개 후보 — 외부 감사 5회 반영 완료.

위키 스키마 재구성(2026-07-17):
- **frontmatter 스키마 난립 근본 예방** — 원인: 노트-크론 3종이 `file` 도구를 가져 LLM이 raw frontmatter를 직접 써서 영문 필드·애드혹 스키마(49종)가 난립. ①3크론에서 `file` 제거(생성=jsc·편집=note-set-field, terminal 경유만) ②SOUL·크론에 "정본 도구 전용, write_file 금지" 강한 규율 ③`bin/normalize-postings.py`(동의어→정본·중복드롭·상태값 한글화·코어필드 보장, 값 보존) 백스톱 + job-collect가 매 수집마다 정규화하고 변칙 N건이면 **경고**(소리나는 감지). 1회 정리로 273노트 정규화(48→26스키마). 완전 airtight(terminal echo)는 못 막으나 쉬운 우회 제거 + 즉시 가시화.
- **normalize 2버그 수정(적대적 검토 발견)** — ①공고 링크 URL에 `---`가 들어가면(`sales-specialist---lakebase`) `split("---",2)`가 그걸 frontmatter 구분자로 오인해 노트를 스킵/절단 → 줄-앵커(`\n---`) 파싱으로 수정. ②태그 값 정규화 추가(영문 job-posting 제거·정본 {구직,공고,task} 보장) → Dashboard 태그쿼리에 전 노트(417/417) 잡힘.
- **postings 변형중복 방지** — jsc의 find_note는 norm_key(공백·하이픈·괄호·법인격·별칭 정규화)로 이미 dedup하나, jsc를 안 거친 생성(에이전트 직접)이 변형 중복을 남길 수 있다. `bin/dedup-postings.py`(norm_key 그룹핑 → canonical 유지·나머지 아카이브·링크 병합, 퍼지매칭 안 함)를 job-collect 훅으로 걸어 매 수집마다 자가치유. 실측: Coupang⇄쿠팡(주) 크로스변형까지 수거.
- **jobs → job-hunting** — 구직 모듈 이름 명확화(cron jobs·employment 혼동 제거) + 면접·리서치 흡수. POSTINGS 경로·스킬 참조·크론·SOUL·smoke 전량 갱신.
- **logs → journal** — 루트 `log.md`(변경로그)와 폴더명 충돌 해소.
- **areas → profile** — 생활 도메인(health·finance·mood·relationships·home·hobby·career·learning)을 "나에 관한 전부"로 profile에 통합. `areas`·`people`(빈 버킷) 소멸.
- **면접 통합** — `projects/interview`→`job-hunting/interview`(준비), `concepts/interviews`+코칭+템플릿→`job-hunting/research`(리서치). concepts는 외부 지식 전용으로 환원.

외부 감사 후속(2026-07-17):
- **CI 린트·보안 추가** — ruff(버그 세트 E9·F만, 스타일 노이즈 없음)+pip-audit(의존성 취약점) 잡 신설. 도입 즉시 실버그 2건 적발: `wiki-skills-sync.py` `import sys` 중복(F811), `kit-doctor.py` `re` 미import(F821, 컴파일은 통과하나 런타임 크래시). mypy는 코드가 타입 미부착이라 노이즈만 되어 제외(정직).
- **Composio 문서 모순 제거** — SKILL.md가 "메타 도구 7개만 올라가 필터링 불필요"라 해놓고 바로 아래 "MCP Tool Filtering(highest impact) — gmail/calendar/drive만 체크"라고 모순 안내(사용자가 헛최적화). 스테일 섹션 삭제 + 참조 정리.
- **Gmail 외부쓰기 게이트 문서화** — 코드로는 외부 MCP(Composio) 호출을 못 막음을 명시하고, 진짜 게이트로 **관찰 기간 Gmail 읽기전용 스코프 연결**(쓰기 도구가 스코프 부족으로 실패=프롬프트 인젝션에도 불변) 권고를 README·CUSTOMIZE에 추가.
- **cron expr 형식 검증** — kit-doctor에 schedule.expr 5필드 검사 추가(필드 수 틀리면 "안 뜨거나 엉뚱한 시각" 무음 실패). /opt/data 잔여 코드 하드코딩 1건(wiki-skills-sync 스킬 경로) DATA 변수화. (프롬프트 리터럴 27건·통합테스트=살아있는 Hermes 필요는 정직하게 잔존.)

이식성·문서 정합(2026-07-17):
- **DATA 해석 HERMES_DATA 우선** — `DATA = "/opt/data" if isdir else HERMES_DATA`가 `/opt/data/wiki` 있으면 env를 무시해서, 컨테이너에서 스모크가 격리 못 하고 6~21축을 통째 건너뛰었다(5개만 실행). → `os.environ.get("HERMES_DATA") or "/opt/data"`로 뒤집어 명시 env가 이기게(**9파일** — bin·scripts 8 + `skills/job-search/jobfilter.py` 1. 첫 커밋이 skills/를 안 훑어 jobfilter를 놓쳤던 것을 후속 수정). live 무영향(HERMES_DATA 미설정 확인). 스모크 컨테이너 조기종료 제거 → 21축 전부 격리 실행
- **서브프로세스 인터프리터 상속** — job-collect·jsc가 자식(recalc·fetch_jd·스크래퍼)을 bare `["python3", ...]`로 띄워, 부모가 venv여도 자식이 시스템 python(PyYAML 없음)으로 새면 크래시했다 → `[sys.executable, ...]`로 부모 인터프리터 상속. (배포측 별개 이슈: 크론이 스크립트를 부르는 `python3`가 PyYAML 있는 인터프리터인지 확인할 것 — requirements.txt가 PyYAML을 선언)
- **CUSTOMIZE 활성화 안내 정정** — "DRY-RUN 문단 삭제=실동작"은 위험(로컬 쓰기는 `.kit-live` 별도 게이트라 문단만 지우면 Gmail만 켜지고 노트는 DRY로 남아 상태 어긋남) → `.kit-live` 단일 로컬 스위치 + Gmail은 마지막 외부쓰기 단계로 분리, 순서 엄수 경고. 스크래퍼 확장 계약도 마크다운 정규식→`--json` 정규화 리스트로 현행화(문서대로 새 보드 추가 시 수집 실패하던 것)
- **MAX_BYTES 실적용** — wanted_search·similar_jobs가 상한을 선언만 하고 `json.load(r)`로 전체를 읽던 것 → `json.loads(r.read(MAX_BYTES))`(다른 스크래퍼와 동일)
- **platforms:[linux]** — job-search 스킬 7종이 `[linux, macos, windows]`로 표기됐으나 키트는 fcntl·chown·bash·/opt/data 의존 Linux 전용 → 정정
- **Gmail DRY-RUN 필수화** — Gmail 읽음·라벨·휴지통은 외부 서비스(Composio)라 쓰기 스위치로 못 막고 되돌릴 수도 없다. 관찰 기간을 "권장"에서 "필수"로 프레이밍 강화(README·CUSTOMIZE)
- **위키 폴더 통합** — inbox(실사용 0, 실시간 로깅이 대체) 폐기 · `_attachments/resume`→`raw/attachments/resume`(원본 자료를 raw로 단일화) → raw(원본 자료)/_archive(지나간 노트) 2계층. SCHEMA·index·SOUL·JobMatch·audit 참조 갱신
- README 스모크 축 수 18→21 통일(CHANGELOG와 불일치 해소)

5차 감사 반영:
- **수집기 lost update 수리** — job-collect가 상태 3종을 락 없이 통째쓰기 → jsc와 `.jsc.lock` 공유 + 저장 시 디스크 재읽기·델타 병합. 수집 중 커밋된 공고가 대기열로 되살아나던 버그
- **설치기 소유권 침범 수리** — `chown -R` 제거, 새로 만든 파일·디렉터리만 chown(기존 유저 파일 불가침, root 실검증). 깨진 심볼릭 링크 대상에 쓰는 경로도 차단
- **삭제 → 수거** — job-notes-cleanup이 영구삭제 대신 `.backups/<날짜>/stale-postings/`로 이동(수집기 reap과 동일 관례)
- **조용한 실패 제거** — 스크래퍼 rc≠0·예외를 집계해 stderr 경고("0건 수집"과 "보드 전멸" 구분)
- eagle-eye 필수 의존성 `jieba` 선언(폴백 없는 import라 없으면 리트리버 사망), mirror=백업미러/master=배포 정체성 명시
- 스모크 7축 → **8축**(수집기 동시성·스크래퍼 실패 보고 회귀 — 수리 전 코드에서 실패함을 확인)

17차 (감사 P0 — SOUL 링크·선톡 영구정지):
- **[높음] install.sh SOUL.md 심볼릭 링크 탈출** — 디렉터리는 막았는데 SOUL.md 자체는 안 봐서, SOUL.md가 외부 링크면 운영규칙 append가 볼트 밖 파일을 수정(실측 5.5KB). → SOUL.md 심볼릭 링크·볼트 밖이면 중단. 스모크 회귀 실측
- **[핵심] 선톡 손상 상태 영구 정지** — 16차에서 "손상 시 침묵"으로 고쳤으나 파일을 안 치워서 다음 실행도 같은 손상을 읽어 **영원히 침묵**(내가 만든 불완전 수리). → `.corrupt-<ts>`로 격리 후 침묵 → 다음 실행은 파일이 없어 재생성. 격리→재생성 실측
- **선톡 상태 flock** — 원자성만 있고 잠금이 없어 동시 실행 lost update 가능 → flock 추가(다른 상태파일과 같은 안전수준)
- **kit-doctor UID 10000 쓰기 검사** — install.sh는 보는데 doctor는 안 보던 것 → 추가(설치기와 같은 판정)
- **회귀 3종**(감사 지적) — 스모크 18→**21축**: SOUL 심볼릭 링크 탈출·sunteok 손상 격리·daily-log 원자 저장. CHANGELOG "실측"만이 아니라 CI가 재발을 잡게
- **보류(구조적, 근거)**: 실서비스 통합테스트=살아있는 Hermes 필요 · /opt/data·UID 결합=대규모 마이그레이션 · 36KB 크론 병합=kit-doctor로 완화(스키마 생성기는 P1 대작업) · pytest/lint/shellcheck 분리=스모크 21축+CI 매트릭스로 갈음(취향)

16차 (감사 P0 — 상태 저장 안전성):
- **sunteok 상태 저장 원자성 + 손상 침묵** — `write_text` 직접 저장(중단 시 부분 JSON)·손상 시 `{}` 초기화(재발송 위험)였음 → tmp+os.replace 원자적 저장 4곳, 손상 시 `{}` 대신 **그 실행은 침묵**(notified_for_ts를 못 가리니 재발사 안 함). 손상 상태 침묵 실측
- **daily-wiki-log 해시 저장 write_atomic** — write_atomic을 import해놓고 해시 상태엔 안 쓰던 것 → 적용(원자적)
- 스모크 생략 표시 정직화 — jieba 미설치로 eagle-eye 검사 생략 시 "통과"로 안 세고 `SKIPPED`에 담아 최종에 "N개 생략(통과 아님)" 명시
- README 축 수 통일(13축→18축)
- **보류(근거)**: 실서비스 통합테스트=살아있는 Hermes 필요(인프라, README 명시) · /opt/data·UID 렌더링=대규모 마이그레이션(README에 "사실상 /opt/data" 명시) · JSON Schema 생성기=kit-doctor로 갈음 · pytest/ruff/shellcheck 분리=스모크+CI로 충분(취향)

15차 (유저 요청 — IP 차단·중복):
- **IP 차단 우회 프록시 슬롯** — 원티드(CloudFront WAF)·유튜브가 VPS 클라우드 IP를 차단(실측: 403/RequestBlocked, 헤더로 안 뚫림). `HERMES_SCRAPER_PROXY` env 하나로 스크래퍼·유튜브만 프록시 경유(LLM 호출은 직접 — job-collect가 자식 subprocess에만 주입). urllib은 env를 자동 존중이라 원티드 등은 코드 0, youtube는 proxy_config 슬롯 추가(Webshare·범용 둘 다). 가짜 프록시로 슬롯 동작 실측, 프록시 없으면 현행 유지
- **중복 dedup 강화** — 회사명 영문병기(`네이버(NAVER)`) 제거 + `company_aliases` yaml(당근=당근마켓, 쿠팡=Coupang 등 진짜 별칭을 명시 매핑). 유사도 매칭은 오검출(다른 공고 병합) 위험이라 안 씀 — 규칙+명시 별칭이 안전. 라이브 420노트 중복 0 유지
- 스모크에 dedup 회귀(영문병기·법인격·별칭) 추가

14차 정리 (승인 플랜 — 감사 12·13 잔여 소진):
- **스크래퍼↔수집기 JSON 계약** — 사람용 마크다운(`- **{직무}** · {회사}`)을 정규식으로 재파싱하던 것을 폐지. 5종 스크래퍼 `--json`을 정규화 리스트 `[{"title","company","url"}]`로 통일(wanted는 raw 덤프였던 것 교정, web에 신설)하고 수집기는 `json.loads`만. 회사명의 `·`에서 절단되던 조용한 누락원 소멸. 계약 위반은 실패로 보고
- **수집기 단일 실행 락 + 전역 데드라인** — 수동·크론 중복 실행 시 두 번째는 즉시 스킵. `HERMES_COLLECT_DEADLINE_MIN`(기본 25분) 초과 시 루프를 끊고 모은 것 정상 저장(네트워크 장애 시 수십 분 폭주 방지)
- **install.sh `--upgrade`** — 키트 관리 코드만 교체(교체분 `.backups/upgrade-*/` 보존), wiki·SOUL·yaml·상태·크론 불가침. 재실행해도 보안 수정이 안 올라가던 구멍 해소. 멱등·심볼릭 링크 가드 실측
- **web 보드 기본 옵트인** — DDG 결과의 임의 도메인 본문이 LLM 판정에 들어가는 구조라 allowlist는 기능과 상충 → 기본 보드에서 제거(주석으로 켜는 법+위험 명시)
- wiki-skills-sync를 공용 I/O 계층(noteio)으로 — DRY-RUN·원자성·권한 보존 적용
- kit-doctor에 yaml 정규식 노브 컴파일 검사(깨진 패턴을 실행 전에 짚음), CI를 Python 3.10/3.12 매트릭스로
- 스모크 15 → **18축**(eagle-eye 멱등 회귀 · JSON 계약 왕복 · 단일 실행 락)

12·13차 감사 반영 (+ eagle-eye 근본 원인):
- **🔴 eagle-eye L2~5가 계속 죽어 있던 진짜 원인 규명·수리** — 그동안 "jieba 미설치 탓"이라고 알려졌으나 **오진**이었다(jieba는 `/opt/data/python-site`에 있음). 실제 원인 2개: ①`__init__`이 백그라운드 init 스레드를 띄우는데 `_lazy_init`이 **멱등하지 않아**(리스트 clear 없이 append) 두 번 돌면 `_doc_tokens`가 `_skill_names`보다 길어짐 → `_fts5_search`가 IndexError → 상위 `except`가 삼켜 **모든 쿼리가 빈 결과** ②`_CONFIDENCE_THRESHOLD=0.015`는 사실상 이론상 최대(0.0164)의 91% = '전 레이어 만장일치' 요구. 임베딩 모델이 없는 기본 설치에선 상한이 0.0131이라 **완벽한 매치도 통과 불가**. → init에 락+멱등+상태 초기화, RRF를 살아있는 레이어 가중치로 정규화, 임계값을 '최대의 35%'로 표현(실측: 잡담 쿼리 0.0000 / 맞는 쿼리 56~100%). **결과: `composio gmail 연동` → L2-5 → composio-personal-assistant 반환(최초 동작)**
- **DRY-RUN 구멍 3개** — ①`desuffix_orphans`의 `os.rename`이 스위치 무시(노트 이름이 실제로 바뀜) ②손상 상태파일 `.corrupt-*` 이름변경이 `--list` 같은 조회에서도 실행 ③`daily-wiki-log`가 스위치를 **전혀** 따르지 않음(log.md·해시 갱신) → 전부 가드
- **비공개 노트 삭제 시 파일명 유출** — 수정·생성은 숨기면서 삭제는 "판정 불가"로 공개하던 것 → 해시 상태에 private 플래그를 저장해 삭제분도 이름 숨김(옛 형식 상태면 보수적으로 숨김)
- **커밋 게이트 `except: pass`** — 정규식 오류·설정 파손으로 게이트가 못 돌면 그대로 입고하던 것 → 예외면 입고 거부(ImportError만 예외)
- **make_note 비원자성** → 공용 `write_atomic` 경유. **ats_search MAX_BYTES 선언만 하고 미적용** → 실제 적용. **로테이션 상태** 비원자적·손상 시 침묵 → 원자적 저장 + 손상 보고
- **전 보드 실패인데 exit 0** → 모든 보드 실패 시 exit 2(크론에 '정상, 0건'으로 기록되던 것)
- **우선도 계산**: 실패를 완전히 숨기던 것 → 경고 출력. 깨진 yaml을 조용히 기본값으로 쓰던 것 → 수집기와 같은 fail-closed
- **문서 드리프트**: 🔴`CUSTOMIZE`의 스크래퍼 출력 계약이 **실제와 반대**였음(`- **{회사}** · {직무}` ← 실제는 직무·회사, 그대로 따르면 데이터 오염) · 스킬 문서들이 **7/13 리팩터 이전 위키 구조**(log/daily·entities·comparisons·templates/) 안내 → 현행 구조로 · Rules 템플릿의 라이브 잔재(계정 상태) 제거 · job-match의 리터럴 `\n` 12개 → 실제 줄바꿈 · README 13→15축 · yaml 머리말의 '깨지면 폴백' 서술이 실제(중단)와 반대였던 것 정정
- `kit-doctor`의 jieba 검사가 벤더 경로를 모르고 거짓 경고를 내던 것 수리(라이브 PASS)

11차 감사 반영 (HIGH 2건 — 둘 다 직전 라운드에서 내가 만든 구멍):
- **HIGH: DRY-RUN이 상태 파일을 못 막던 것** — jsc의 `_wjson`엔 가드를 넣고 **job-collect의 `_wjson`엔 빠뜨렸다.** 스위치가 꺼져 있어도 `.job-seen/.job-staging/.job-rejected/.job-rotation`이 써져서, 공고가 "이미 처리함"으로 기록돼 나중에 조용히 누락될 수 있었다(README 보장과도 불일치) → 가드 추가 + 로테이션 카운터도 DRY-RUN이면 전진 금지. 실측: 스위치 OFF 수집 시 상태파일 0개
- **HIGH: 설치기 경로 탈출** — 최종 파일의 심볼릭 링크만 보고 **상위 디렉터리가 링크인 경우를 놓쳤다**(`$DATA/skills` → 외부면 파일 65개가 볼트 밖에 설치됨). → 주요 디렉터리 링크 즉시 중단 + 복사 경로마다 realpath가 설치 루트 안인지 검사(중첩 링크도 차단). 실측: 볼트 밖 0개
- **회귀 테스트 2종 추가**(감사 요청) — 스모크 13 → **15축**: ①스위치 OFF에서 수집기 상태파일 무생성 ②설치기 심볼릭 링크 탈출 차단
- 문서: README가 이미 제거된 플러그인을 "지울 수 있다"고 설명하던 것 정정, **설치 경로는 사실상 `/opt/data` 기준**임을 명시(크론 프롬프트·SOUL의 경로 문자열은 LLM 지시문이라 변수화 불가 — 다른 경로면 설치기가 경고)

10차 감사 반영 (출시 차단 항목):
- **DRY-RUN을 진짜 기본값으로 뒤집음** — 종전엔 모델이 `HERMES_KIT_DRY_RUN=1`을 붙여야만 안전했다(=기본값이 아니었음). 이제 **코드가 기본으로 쓰기를 거부**하고, 사람이 명시적으로 스위치를 켤 때만 쓴다: `touch $DATA/.kit-live`(재시작 불필요) 또는 `HERMES_KIT_LIVE=1`. 켠 뒤에도 `HERMES_KIT_DRY_RUN=1`은 그 실행만 차단. 판정자는 `noteio` 하나(3중 중복 제거). 4시나리오 실측
- **외부 저작 플러그인 2종 배포판에서 제거** — `hermes-snow-search`(LinQuan & Snow)·`rtk-rewrite`(ogallotti/rtk-ai)는 업스트림 커밋·라이선스 확인 불가 → 제거(mirror엔 보존). SOUL의 snow_search 의존도 끊음 → **이제 배포판 전 구성요소가 MIT 원저작**
- **스모크 DNS 의존 제거** — 10번이 실제 DNS를 타서 오프라인·제한망에서 실패하던 것 → resolver 모킹(+DNS가 내부 IP를 반환하는 경우도 차단하는지 검사 추가)
- **`bin/kit-doctor.py` 신설** — 손으로 병합하는 config·36KB jobs.json의 실수를 잡는다: 쓰기 스위치 상태·필수 파일·yaml/상태파일 손상·크론 배달지(chat_id)·placeholder 잔존·의존성. 라이브 실측 PASS(오탐 2종 수정 후)
- README 테스트 수 정정(12→13), CHANGELOG의 "전 구성요소 MIT" 표현을 시점에 맞게 정정
- **통합 테스트 부재는 해결 못 함(정직)** — Hermes 플러그인 로딩·Discord·Composio는 살아있는 스테이징이 있어야 검증된다. README에 "CI가 못 보는 것" 목록으로 명시

8·9차 감사 반영:
- **RTK가 확인 요구(exit 3)를 자동 수락하던 문제(매우 높음)** — 주석은 `3 = ask/confirm`인데 코드가 `{0,3}`을 성공으로 취급해 재작성·실행. **라이브에 rtk 바이너리가 실제 설치돼 있어 이론이 아니었음** → exit 3이면 재작성 포기(원 명령이 Hermes 승인 절차를 그대로 탐). 가짜 rtk로 0/2/3 전 경로 실측
- **rtk-rewrite 기본 비활성화** — 키트 자체 스킬 문서가 "파이프에 rtk를 끼워넣어 명령이 성공해도 exit_code 127"이라 기록해 뒀는데 기본 켜져 있었음. 외부 저작 + 외부 바이너리 필요
- **fail-open → fail-closed** — ①깨진 `search-profile.yaml`로 제작자 기본 필터를 쓰며 계속 수집하던 것 → 중단 ②손상된 상태 JSON을 빈 값으로 간주하고 그 위에 덮어써 **장부가 통째로 날아갈 수 있던 것** → `.corrupt-<ts>`로 원본 보존 후 중단
- **`private: true`가 OS 권한이 아니었음** — 노트·장부가 0644로 생성(같은 서버 다른 계정이 열람 가능) → 신규 파일 0600
- **노트 수정기 경로 가드** — 볼트 밖 경로·심볼릭 링크·`../` 우회 거부(외부 입력에 휘둘린 경로 조작 차단)
- **3번째 URL 영구 유실** — 추가링크 슬롯이 차면 저장 실패인데도 URL을 장부에 넣어 링크가 어디에도 안 남던 것 → 본문 `## 추가 링크`에 적재
- **비공개 노트 파일명이 Discord로 나가던 것** — 파일명 자체가 정보(회사·건강·재정) → `private: true` 노트는 건수만 보고
- **로그 유실 순서 버그** — 해시를 먼저 저장하고 log.md를 나중에 써서, 중간 실패 시 변경 기록이 영구 증발하던 것 → 로그 먼저·fsync 후 상태 전진. 해시 파일 손상도 fail-closed
- 중복 `daily-wiki-log.py`(스킬 내부 구버전 — log.md 자기보고 루프 있음) 제거, 정본은 `scripts/` 하나
- 설치기: 기본 활성 플러그인 의존성(PyYAML·jieba) 검증 + README Quick Start에 `pip install` 단계(설치 PASS인데 기능이 죽어 있던 상태 방지)
- **선톡 재발송 상한** — 주석은 "1번"인데 코드는 6시간마다 무기한 발사였음(스팸) → `MAX_REENGAGE=2` + 카운터, 유저가 한 마디 하면 리셋. 반복 시뮬로 2회 후 침묵 실증. 하드코딩된 크론 잡 ID → `SUNTEOK_JOB_ID` env
- **크론 표시 정합** — `구직 수집+판정`이 실제 하루 2회(`10 8,20`)인데 표시는 4회(`10 8,12,16,20`)였음 → expr을 진실로 통일
- **Composio 문서 모순 해소** — 인증 헤더가 문서마다 `x-api-key`/`x-consumer-api-key`로 갈렸음 → **라이브 실측 정답 `x-consumer-api-key`로 통일**. 토큰 서술도 "500+ 항상 프롬프트에"(스테일) vs "메타 7개만"(정본) 충돌 → 정본으로 단일화
- **설치기 복구 안내 모순** — chown 실패 시 `chown -R ... wiki`를 안내해 자기 원칙(기존 파일 불가침)을 깨뜨리던 것 → root로 재실행 안내로 교체
- note-set-field 독스트링의 "바이트 그대로 보존" 주장 정확화(CRLF·mtime·ACL·중복키 한계 명시)
- **정본 하나만 남기기(문서 드리프트 정리)** — 감사 총평("여러 세대의 문서·코드가 누적") 반영: ①상태 스키마가 스킬마다 달랐음(`예정/진행중/완료` 구형 vs `예정/지원완료/진행중/완료` 현행) → 8파일 통일, 폐기된 `추가링크1~3` 표기도 1~2로 ②**폐기됐다던 `--merge`가 코드엔 살아 있었음** → 5개 스크래퍼에서 제거(옛 마크다운 표를 비원자적으로 쓰는 경로 소멸) ③스테일 경로 참조 정리(daily-wiki-log.sh·wiki/project/) ④키트에 없는 파일 참조(maps 스킬·daily-note 템플릿)는 '선택/미포함'으로 명시
- **응답 크기 상한 공통화** — fetch_jd에만 있던 5MB 상한을 6개 스크래퍼 전부에 적용(사이트 오작동 시 메모리 급증 방어)
- 스모크 → **13축**(fail-closed·경로가드·권한 포함)

7차 감사 반영:
- **노트 I/O 단일 계층 `bin/noteio.py` 신설** — 수집기의 노트 이동·추가링크 쓰기, priority-recalc, job-notes-cleanup이 각자 잠금 없이 파일을 다시 쓰고 있었음(동시 갱신 시 유실). 이제 노트를 바꾸는 모든 경로가 공용 락 + 원자적 저장(권한·소유권 보존)을 거친다. **부모-자식 교착 방지**(jsc가 락을 쥔 채 priority-recalc를 부름 → 환경변수로 보유 상속)
- **비신뢰 입력 경계 명문화** — 공고·메일·JD·포스터의 문장은 자료일 뿐 지시가 아님을 SOUL·크론 프롬프트에 못박고, 주입 시도 발견 시 보고하게 함. 외부 값은 인용 인자로만 전달
- **DRY-RUN 출력 정정** — "갱신 생략"과 "등록 완료"를 동시에 출력해 성공으로 오인시키던 문제 → `[DRY-RUN] 등록했을 것:`으로 통일
- **라이선스 고지 정정(중요)** — "전부 원저작"은 **거짓이었음**: `hermes-snow-search`(LinQuan & Snow)·`rtk-rewrite`(ogallotti/rtk-ai)는 외부 저작. 업스트림 라이선스 미확인 사실을 명시하고 제거 방법 안내
- **SSRF 한계 명시** — DNS 재바인딩은 막지 못하는 '기본 방어'임을 코드·문서에 정직하게 기록
- 죽은 코드 `make_note`(수집기, v7에서 노트 직행 폐지 후 호출부 0) 제거
- 스모크 11 → **12축**(잠금 계층 자체검사·교착 없음)

6차 감사 반영:
- **note-set-field 원자성·잠금** — 핵심 노트 수정기가 원본에 직접 쓰고 있었음(중단 시 파손·동시 갱신 유실) → flock + 임시파일·fsync·os.replace. **권한/소유권 보존**(600 노트가 644로 풀리거나 root 소유가 되는 것 방지). 동시 8건 무유실 실증
- **SSRF 가드** — fetch_jd가 공고·메일에서 온 URL을 검증 없이 열었음 → 사설·루프백·링크로컬(169.254.169.254 등) 차단 + **리다이렉트마다 재검사** + 5MB 상한. 내부주소 5종 차단·실제 보드 3종 통과 실측
- **DRY-RUN을 코드로 강제** — 프롬프트 문단 삭제 의존에서 벗어나 `HERMES_KIT_DRY_RUN=1`이면 두 변경 도구가 쓰기를 차단(Gmail은 외부 서비스라 불가 — 정직하게 문서화)
- **경로 이식성** — 주변부 8파일이 `/opt/data` 하드코딩이라 커스텀 DATA_DIR 설치가 조용히 깨졌음 → 코어와 같은 `HERMES_DATA` 규칙으로 통일(ats 데이터는 자기 파일 기준). 커스텀 경로 실행 검증
- **고아 제거** — cardnews-gen.py·ocr.sh(참조 0, 스킬/OCR 폐기 잔재). SSRF 표면 하나가 함께 사라짐
- **재현성** — `constraints-ci.txt`로 CI만 버전 고정(런타임 하한은 오버레이 특성상 의도)
- 스모크 8 → **11축**(노트 동시성·권한·원자성 / SSRF / DRY-RUN 강제)

출시 전 개선(5차 후속):
- **배포판 정리** — Hermes 플랫폼 지식 스킬 3종(hermes-agent·model-routing·skill-authoring) 제외: 파이프라인 의존 0, 업스트림 공식 문서와 중복, 버전 종속. 죽은 카테고리 스텁 7개(스킬 0종인 DESCRIPTION.md)도 제거 → **18스킬**(당시 외부 플러그인 2종은 잔존 — 아래 10차에서 제거하며 비로소 전 구성요소 원저작이 됨)
- **tone-pass 배포판에서 제외** — 최종 답변 전문을 외부 LLM에 보내는 플러그인. 메인이 딥식 계열이면 자기 응답을 스킵하는 구조라 실사용 조건이 좁고, 켜는 순간 프라이버시 노출이 커서 배포판에서 뺐다(백업 미러엔 보존).
- **Gmail·노트를 바꾸는 크론 2종은 DRY-RUN(읽기 전용)이 기본** — 켜도 처음엔 하려던 동작만 보고. 3~7일 확인 후 문단 삭제로 실동작 전환
- **SOUL 이식 방식 확립** — 기존 SOUL.md는 절대 덮지 않고, 파이프라인 동작 조건인 **운영 규칙 블록만 자동 append**(캐릭터 불가침·원본 백업·멱등). 놓쳐서 시스템이 정체되는 걸 막되 남의 페르소나는 안 건드림
- **설치 권한 실검증** — chown 실패가 warn+exit 0으로 넘어가던 것을, uid 10000이 실제로 쓸 수 있는지 검사해 FAIL+exit 1(실측: 정상 볼트 PASS·root 전용 볼트 FAIL)
- 공급망: CI 액션을 커밋 SHA로 고정. requirements는 오버레이 특성상 하한 유지가 의도임을 근거와 함께 명시
- README에 **검증 범위** 표 추가 — CI가 검사하는 것 / 실제 Hermes·계정이 필요해 검사 못 하는 것(플러그인 로딩·사이트 응답·Composio·vision·eagle-eye L2~5)을 정직하게 구분
- 구직 파이프라인(수집→스테이징→판정→입고), 위키 시맨틱 스키마, 선톡·크론 템플릿, eagle-eye 플러그인
- 설치기 `install.sh`(파일단위 보강·PASS/FAIL 판정), 스모크 테스트 7축, GitHub Actions CI
- 데이터 무결성: 상태파일 원자쓰기+flock 직렬화, YAML-안전 직렬화(신규 노트·frontmatter 편집기)
- 안전 기본값: 크론 전 잡 비활성, 메일·노트 변경 잡은 DRY-RUN 기본, 보수적 config 예시
- LICENSE(MIT)·THIRD_PARTY_NOTICES·requirements 계층화
- 브랜치: `master` = 배포판(코어+구직 18스킬) · `mirror` = 볼륨 전체 백업(범용 스킬 포함, 설치용 아님)
