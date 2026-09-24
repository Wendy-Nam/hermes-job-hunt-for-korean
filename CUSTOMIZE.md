# 사용자 설정

기본값은 한국 구직 검색 예시입니다. 아래 순서대로 바꾸면 공고 수집부터 맞춤 이력서 생성까지 같은 기준을 사용합니다.

## 1. 검색 프로필

원본: `bundle/config/search-profile.yaml`
설치본: `/opt/data/search-profile.yaml`

- `keyword_blocks`: 검색어와 시노님
- `title_must_match`: 지원 직군 제목 게이트
- `senior_signals`, `title_exp_min_years`, `body_exp_min_years`: 연차 기준
- `exclude_roles`: 제외 직군
- `track_bonus`: 선호 직군별 우선순위 가중치
- `boards`: 사용할 수집 보드
- `company_aliases`: 같은 회사의 법인명·브랜드 중복 제거

기본 복사본은 첫 설치 때만 만들어집니다. 업그레이드해도 덮어쓰지 않습니다.

## 2. 공고 판정

원본: `bundle/vault/automation/job-hunting/Rules.md`
설치본: `/opt/data/wiki/automation/job-hunting/Rules.md`

지원 가능·보완 후 지원·제외 기준과 회사 등급을 정합니다. 공고는 AI 판정 전에는 볼트에 들어가지 않고, 판정 대기열에서 신원을 확인한 뒤 생성됩니다.

## 3. 수집기·보드

코드: `bundle/job-search/scripts/`, `bundle/job-search/skills/job-search/`

- `wanted-search`: 원티드
- `kr-search`: 사람인·잡코리아
- `linkedin-search`: LinkedIn
- `ats-search`: Greenhouse·Lever·Ashby
- `web-search`: 일반 웹 검색
- `job-hunter-kit`: 독립 설치형 수집 다이제스트

보드 스크립트는 `--json`으로 `title`, `company`, `url`을 반환해야 합니다. 다른 지역으로 옮길 때는 해당 보드와 `boards` 설정을 교체합니다.

## 4. 맞춤 이력서·자소서 재료

설치 후 다음 파일을 채웁니다.

```text
wiki/automation/job-hunting/resume/
├── master_resume.md       경력·역할·성과
├── fit_evidence.md        공고 요구사항별 근거
├── role_contexts.md       직무별 강조점
└── portfolios.md          프로젝트·링크
```

공고 URL과 `role_contexts.md`를 지정해 생성합니다.

```bash
python3 /opt/data/bin/generate-tailored-resume.py \
  --job-file /path/to/job.md \
  --out /path/to/output \
  --lang ko
```

생성기는 마스터 자료의 모든 내용을 복사하지 않고 공고와 맞는 프로젝트·역량·달성 지표만 골라 국문 또는 영문 HTML로 구성합니다. 웹서치·회사 리서치 결과는 `company-interview-research`, `web-reader`, `youtube-content`로 별도 수집합니다.

## 5. 메일과 지원 상태

`bundle/cron/jobs.json`에서 필요한 작업만 골라 `/opt/data/cron/jobs.json`에 병합합니다. 예시는 모두 `enabled: false`입니다.

- 지원 접수: 공고 노트 상태를 지원완료로 변경
- 면접·과제: 진행중으로 변경하고 일정 리마인더
- 불합격: 완료로 변경하고 결과 요약
- 회사명이 모호하면 자동 변경하지 않고 확인 알림만 생성

## 6. 크론·모델·채널

원본 예시:

- `bundle/cron/jobs.json`
- `bundle/config/config.example.yaml`

채널 ID, 모델, API 키,vision 지원 여부를 본인 값으로 바꾸세요. 외부 서비스 쓰기 권한은 DRY-RUN 관찰이 끝난 뒤에만 활성화합니다.

## 검증

```bash
python3 tests/smoke_test.py
python3 bundle/job-search/skills/job-search/job-hunter-kit/tests/test_hunt.py
python3 /opt/data/bin/generate-tailored-resume.py --selftest
python3 /opt/data/bin/kit-doctor.py /opt/data
```

## Obsidian 대시보드

볼트 템플릿은 TaskNotes·Dataview·Tasks 플러그인 설정을 포함합니다. 공고 노트에 `공고` 또는 `posting` 태그를 붙이면 `Dashboard.base`의 보드와 테이블에 나타납니다.
