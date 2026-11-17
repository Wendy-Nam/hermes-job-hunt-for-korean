# Hermes Job Hunt for Korean · v2.1.0

Hermes에서 한국 채용 공고를 수집하고, 경력 근거로 적합도를 판단하며, 지원 현황·문서·면접을 관리합니다. 선택적으로 Aside 브라우저에서 저장 이력서 선택과 지원 폼·최종 제출을 실행합니다.

## 구성

| 실행 환경 | 역할 |
|---|---|
| Hermes · Linux/macOS | 공개 공고 수집, 판정, 경력 자료, 문서·면접, 메일·리마인더 |
| google-workspace 또는 Composio | 사용자가 선택한 Gmail 연결의 검색·읽기 |
| Aside · macOS/Windows | 로그인된 채용 플랫폼의 이력서 선택·폼 작성·승인 범위 내 제출 |

공개 모델 분석은 선택 사항입니다. 개인 경력·메일은 사용자가 지정한 개인 데이터 처리용 모델에만 전달합니다. 샘플에 특정 모델 라우터·계정·Discord 채널을 강제하지 않습니다.

## 설치

Python 3.10+, PyYAML, requests와 이미 설치된 Hermes가 필요합니다. Hermes의 Python 환경에서 실행하세요. 기존 공용 스크립트나 전역 설정을 덮어쓰지 않는 native 설치를 권장합니다.

```bash
git clone https://github.com/Wendy-Nam/hermes-job-hunt-for-korean.git
cd hermes-job-hunt-for-korean
python3 native/install.py \
  --home "$HERMES_HOME" \
  --job-root "/path/to/your/vault/job-hunting" \
  --mail-backend google-workspace \
  --profile-file profiles/korean-junior-ai.yaml \
  --dry-run
```

미리보기를 확인한 뒤 `--dry-run`을 빼고 실행합니다. `HERMES_HOME`은 실제 프로필 홈을 지정하세요. 기본 프로필을 사용하는 일반 설치는 `~/.hermes`, 컨테이너는 해당 데이터 볼륨 경로입니다. `./install.sh --native ...`도 같은 인자를 받습니다.

메일을 사용하지 않으면 `--mail-backend none`, Composio를 사용하면 `--mail-backend composio`를 선택합니다. Google Workspace 선택 시 기존 `google-workspace` 스킬 설치와 OAuth 인증이 필요합니다. 이 저장소에 토큰을 복사하지 않습니다.

설치기는 다음 경로를 사용합니다.

```text
HERMES_HOME/
├── apps/job-hunt/          코드·설정·문서 서식
├── state/job-hunt/         대기열·장부·공개 분석·쓰기 스위치
├── skills/job-search/     공통 스킬 5개
└── scripts/job-hunt/       Hermes 크론의 작은 실행기
```

지정한 구직 폴더에는 **없는 서식만** 보강합니다. 기존 Rules, 이력서, TaskNotes 칸반과 기록은 보존합니다. 개인 설정·검색 프로필은 재설치로 교체하지 않습니다. 바뀐 관리 코드는 백업한 뒤 갱신합니다.

## 첫 설정

1. 앱의 `config/search-profile.yaml`과 구직 폴더의 `Rules.md`를 본인의 조건으로 조정합니다. 제공 프로필은 주니어 AI 교육·자동화 실무/PM·GTM/RevOps의 예시입니다.
2. `resume/`의 마스터 경력·검증 근거·직무 맥락·포트폴리오를 채웁니다. 예시 인물의 사실을 본인 경력으로 사용하지 않습니다.
3. 앱의 `config/mail.yaml`에서 선택한 백엔드를 확인하고 `job-hunt mail-check`로 연결 준비 상태를 확인합니다. Composio의 실제 MCP 연결은 Hermes에서 선택 계정으로 확인합니다.
4. 앱의 `config/models.yaml`에 본인의 공개/개인 모델 경로를 지정합니다. OmniRoute 사용자는 `examples/models.omniroute.yaml`을 참고하되 본인에게 존재하는 라우트명으로 바꿉니다.
5. 읽기 전용 관찰 뒤 `HERMES_HOME/state/job-hunt/.kit-live`를 만들면 노트·상태 쓰기를 허용합니다. 기존 전역 `.kit-live`를 승계하지 않습니다.

```bash
"$HERMES_HOME/apps/job-hunt/job-hunt" status
"$HERMES_HOME/apps/job-hunt/job-hunt" mail-check
"$HERMES_HOME/apps/job-hunt/job-hunt" collect
"$HERMES_HOME/apps/job-hunt/job-hunt" queue
```

## 스킬과 자동화

- `/job-search`: 보드별 검색·수집
- `/job-match`: 공고와 실제 경력 비교
- `/job-application`: 선택한 메일 백엔드로 상태 확인·리마인더
- `/job-documents`: 이력서·자소서·포트폴리오 생성
- `/job-interview`: 회사 조사·면접 연습·복기

Hermes의 Python 환경에서 `native/cron-jobs.py --app "$HERMES_HOME/apps/job-hunt" --channel YOUR_CHANNEL_ID`를 실행하면 세 크론을 일시중지 상태로 준비합니다. 개인 데이터 처리용 모델과 메일 인증을 확인한 뒤 `--activate`로 활성화합니다. 작업 시각은 Hermes에 설정한 타임존을 따릅니다. 스크립트 경로·모델·채널을 직접 jobs.json에 복사하는 방식은 사용하지 않습니다.

## 문서 출력과 로컬 PDF

설치된 `job-hunt documents`를 사용합니다. `work/` 아래 staging 사본은 실행하지 않습니다. 일반 출력은 원본 경력·생성기 코드를 수정하지 않습니다.

```bash
"$HERMES_HOME/apps/job-hunt/job-hunt" documents \
  --company "직무별 예시" --position "AI 교육 담당자" \
  --jd-text "AI 교육 실습 워크숍 교육자료 콘텐츠 설계" --lang ko --only resume
```

기본 출력은 HTML이며, PDF가 필요할 때만 `--pdf`를 붙입니다. PDF 변환은 설치된 Chrome/Chromium을 사용하고 한 번에 한 문서씩 처리합니다. 문서별 60초 기한을 넘기면 브라우저 자식 프로세스까지 정리합니다. PDF 실패 시에도 HTML 파일과 경로는 남깁니다. 외부 렌더링 SaaS는 필수가 아닙니다.

Debian 컨테이너의 이미지 빌드 단계 또는 컨테이너 내부 root에서 실행할 설치 명령:

```bash
apt-get update
apt-get install -y --no-install-recommends chromium fonts-noto-cjk
```

다른 경로의 브라우저는 `CHROME_PATH`로 지정합니다. 컨테이너 안에서만 설치한 패키지는 컨테이너 재생성 시 사라질 수 있으므로 운영 이미지의 Dockerfile에도 반영하세요. 설치기는 시스템 패키지 설치나 컨테이너 재시작을 자동 실행하지 않습니다.

## 확장 이력 자료

[내용 구성 템플릿](docs/resume-content-template.md)에서 역량·업무·프로젝트·활동에 어떤 내용을 적을지 확인할 수 있습니다.

`resume/role_brief.template.md`는 제공한 원본처럼 한 줄 소개, 역량명과 수행 내용 5개, 경력별 주요 업무, 학력·자격, 프로젝트, 대외활동, 기술군을 연결하는 내용 틀입니다. `요약 방식: 역량`은 역량 뒤에 에피소드 지표를 자동 덧붙이지 않습니다. `주요 업무`와 정성적인 `결과`도 입력할 수 있어 숫자를 만들 필요가 없습니다.

마스터 서식에 교육·코칭, 오픈소스·커뮤니티, 연구 경험, 콘텐츠 선택란을 제공합니다. 기존 대외활동·발표·교육 이수·수상 등도 유지합니다. `### 섹션명`과 `|`로 나눈 행을 사용하며, 빈 섹션과 `숨길 항목`에 적힌 섹션은 출력하지 않습니다. 국문은 표, 영문은 항목형으로 표시합니다.

`resume/source_catalog.md`, `evidence_ledger.md`, `claim_conflicts.md`에서 원본 파일·페이지·주장·측정/추정/후기·수치 충돌을 관리합니다. 개인 원본과 이 장부의 실제 내용은 비공개 구직 폴더에만 둡니다.

직무·언어별 자료 4종을 준비했다면 정본을 고치지 않고 해당 사본으로 출력할 수 있습니다.

```bash
"$HERMES_HOME/apps/job-hunt/job-hunt" documents \
  --materials-dir "/path/to/private/role-materials" \
  --position "AI 교육 담당자" --jd-text "실습 교육 코칭" --only resume
```

해당 폴더에는 `master_resume.md`, `fit_evidence.md`, `role_contexts.md`, `portfolios.md`가 모두 필요합니다. 누락 시 다른 이력서로 대체하지 않습니다. `--lang en`은 서식을 선택하므로 영문 본문은 영문 자료로 준비합니다. 포트폴리오에는 `성과 근거`로 직접 측정·추정·후기 같은 성격을 함께 표시할 수 있습니다.

## Aside 지원 실행

[`integrations/aside/aside-job-apply`](integrations/aside/aside-job-apply)에 별도 표준 스킬을 제공합니다. 플랫폼에 저장된 이력서 버전을 실제 내용과 JD로 비교하고, 지원 폼·문항·첨부를 처리합니다. 명시적으로 승인한 계정·지원 범위·조건·횟수·기한 안에서 최종 제출할 수 있습니다.

정책 예시는 작성 모드입니다. 실제 자동 제출은 본인의 정책을 설정한 뒤 사용합니다. Python 표준 SQLite 이력으로 중복·제출 한도·확인 불가 상태를 관리합니다. 저장소 설치가 실제 지원을 실행하지는 않습니다. 실행 방법은 [Aside 안내](docs/aside.md)를 보세요.

## 업그레이드·제거

같은 홈과 구직 루트로 설치기를 다시 실행합니다. 변경 코드의 이전 사본은 `HERMES_HOME/backups/job-hunt-*`에 보관합니다. 개인 설정·노트는 유지하며 과거 지원 기록을 자동 정리하지 않습니다.

```bash
python3 native/uninstall.py --home "$HERMES_HOME" --dry-run
```

제거 전 구직 크론을 일시중지해야 합니다. 제거기는 매니페스트의 변경되지 않은 관리 코드만 백업으로 이동하며 노트·설정·상태·사용자 파일은 남깁니다. 직접 수정한 코드나 잘못된 경로는 제거하지 않고 중단합니다.

## 검증과 호환성

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/smoke_test.py
```

CI는 Python 3.10·3.12에서 검증합니다. Aside의 실제 로그인·페이지 조작·접수는 별도 실행 검증이 필요합니다.

기존 v1 overlay 설치의 `./install.sh DATA_DIR`, `./uninstall.sh DATA_DIR`는 호환용으로 남겨 둡니다. 신규 설치는 native 방식을 사용하세요. 자세한 설정은 [CUSTOMIZE.md](CUSTOMIZE.md), 변경 내용은 [CHANGELOG.md](CHANGELOG.md)를 참고하세요.

## 서식 예시

`docs/examples/`에는 가상 인물로 만든 국문·영문 이력서, 자소서, 포트폴리오 예시가 있습니다. `python3 docs/examples/build_examples.py`로 다시 만들 수 있습니다. 실제 개인 자료·메일·브라우저 로그인·제출 이력은 공개 저장소에 넣지 않습니다.
