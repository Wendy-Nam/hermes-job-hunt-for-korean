---
name: job-hunter-kit
description: "Use when the user wants Korean job postings: /jobs search, periodic crawl, Telegram digest. Self-contained: vendored wanted+web boards, profile filter, seen-state. Youtube lookup stays in youtube-content."
version: 2.0.0
author: seoa
platforms: [linux, macos]
metadata:
  hermes:
    tags: [구직, 채용, jobs, wanted, telegram, digest]
    related_skills: [youtube-content]
---

# Job Hunter Kit v2 — 자립형 구직 플러그인 (가벼운 입문용)

> 풀 파이프라인(수집→AI판정→위키 노트→메일 리마인더)이 필요하면
> `scripts/job-collect.py` + `cron/jobs.json`의 `구직 수집+판정` 잡을 쓴다 (README ①②).
> 이 킷은 그 전 단계 — **설치 5분 만에 원티드+웹 공고를 텔레그램으로 받는**
> 최소 자립 묶음이다. 위키·Gmail·판정 없이 동작한다.

외부 `job-search/*` 스킬에 의존하지 않는다.
보드 스크립트는 `scripts/boards/`에 벤더링, 필터는 `profile.yaml` 1개.
이 폴더만 복사하면 남의 머신에서도 동작한다.

## 구조

```
job-hunter-kit/
  SKILL.md                  이 파일 (설치·사용·제외목록)
  install.sh                번들 설치기 (테스트→보드확인→크론등록)
  profile.example.yaml      프로필 예제 (복사 → profile.yaml)
  scripts/hunt.py           통합 검색 (유일 진입점)
  scripts/digest.py         no-agent 크론용 (신규만 Markdown, 없으면 빈 출력)
  scripts/boards/wanted_search.py   원티드 공개 API (무인증)
  scripts/boards/web_job_search.py  DuckDuckGo HTML (무인증)
  tests/test_hunt.py        자가 테스트 (외부망 없이 실행)
```

의존성: python3 표준라이브러리만 (urllib, argparse, json, re).
pyyaml은 선택 (없으면 `senior_signals` 한 줄 파서로 폴백).
유튜브 기업리서치는 원본 스킬 그대로 사용 (선택):

`profile.yaml`, `seen.json`이 있는 `state-dir`은 깃에 넣지 않는다.

## 설치 — 번들 한 방 (`install.sh`)

준비물은 이 폴더 1개 + python3만. 크론·래퍼·상태까지 설치기가 묶어서 처리한다.

```bash
cd job-hunter-kit
./install.sh --keyword "백엔드" --schedule "0 8 * * *" --deliver telegram
# --deliver: telegram | discord | local (기본 local — 텔레그램 미연동 머신도 설치 가능)
# --dry-run: 실제 등록 없이 명령만 출력
# --uninstall: 크론 + 래퍼 제거 (스킬·seen 상태는 유지)
```

설치기가 하는 일 (3단계, 실패하면 중단):

1. `tests/test_hunt.py` 실행 (외부망 없이)
2. `hunt.py --boards wanted` 실동작 1건 확인 (원티드 API 살아있나)
3. `~/.hermes/scripts/job-hunter-digest.sh` 래퍼 생성 + `hermes cron create --no-agent` 등록

크론은 **no-agent**다: LLM을 안 깨우고 `digest.py`의 stdout만 배달.
신규 없으면 빈 출력 → 미발송 (조용한 크론). 1회 발송 내용은 `seen.json` 장부로 중복 차단.

```bash
# 설치 후 확인
hermes cron list | grep -F '구직 다이제스트'
~/.hermes/scripts/job-hunter-digest.sh | head   # 신규 없으면 빈 출력이 정상
hermes cron doctor
```

> ⚠️ `Gateway is not running` 경고가 뜨면 크론 시각에 자동 발화 안 됨.
> `hermes gateway install` 후 `start` 필요 (서버) — 로컬 테스트는 `hermes cron run <id>`로 수동 발화.

```bash
uv run python3 ~/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py "<URL>" --text-only
```

## 사용

```bash
# 대화형 (텔레그램용 마크다운)
python3 scripts/hunt.py "백엔드" --limit 10
# 크론/파이프라인 (JSON)
python3 scripts/hunt.py "백엔드" --limit 10 --json
# 신규만 (다이제스트용 — seen.json 장부 사용)
python3 scripts/hunt.py "백엔드" --state-dir ~/.hermes/job-hunter --new-only --json
# 보드 선택 / 타임아웃 / 필터 해제
python3 scripts/hunt.py "백엔드" --boards wanted --timeout 20
python3 scripts/hunt.py "백엔드" --all-levels
# 내 프로필 지정
python3 scripts/hunt.py "백엔드" --profile ./profile.yaml
```

종료코드: 0=정상, 1=사용법 오류, 2=보드 전부 실패.

## Cron 배선 예

```json
{"name": "구직 다이제스트", "schedule": {"expr": "0 8 * * *"},
 "prompt": "job-hunter-kit hunt.py를 --state-dir ~/.hermes/job-hunter --new-only --json으로 실행하고 신규만 deliver로 텔레그램 전송"}
```

## 제외된 것 (호출 안 함 — 파일은 각자 자리에 유지)

- `kr-search` (사람인/잡코리아 HTML 파싱) — 구조 변경·403 취약
- `linkedin-search` (게스트 엔드포인트) — 차단율 높음
- `ats-search` — 외국계 한정
- `apify-search` — API 키 필요
- `cover-letter-pdf`, `interview-grill`, `job-evaluation-framework`, `job-match`, `company-interview-research`, `demo-*`, `real-job-demo` — 서류/면접 파이프라인 (수집과 분리)

## Verification

```bash
python3 tests/test_hunt.py   # 외부망 없이 통과해야 함
python3 scripts/hunt.py "백엔드" --limit 3
```

