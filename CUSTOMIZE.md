# 사용자 설정

## 검색과 판단

처음 설치할 때 `--profile-file`로 YAML을 선택합니다. 설치 후에는 앱의 `config/search-profile.yaml`을 직접 조정합니다. 재설치 때 원본 예제로 되돌리지 않습니다.

- `keyword_blocks`: 회차별 검색어
- `boards`: 사용하는 보드
- `filters`: 제목·연차·제외 직군 기준
- `track_bonus`: 직무별 우선순위
- `company_aliases`: 검증된 회사 별칭

개인 지원 조건과 판단 원칙은 구직 폴더의 Rules.md에 둡니다. 본문 필수 경력과 우대 경력을 구별하고 모르는 값을 추정하지 않습니다.

## 메일 백엔드

앱의 `config/mail.yaml`에서 하나를 고릅니다.

```yaml
backend: google-workspace
```

- `google-workspace`: 설치된 동일 이름 스킬의 setup.py와 google_api.py를 사용합니다. gws 실행과 Python 폴백은 해당 래퍼가 관리합니다. 크론이 google-workspace와 job-application을 함께 로드합니다.
- `composio`: Hermes에 설정한 Composio MCP의 선택 계정으로 검색·읽기합니다. 모든 연결 계정을 무조건 순회하지 않습니다.
- `none`: 메일에 접근하지 않고 노트 기반 리마인더만 만듭니다.

인증 실패 시 다른 백엔드나 계정으로 바꾸지 않습니다. 메일 발송·회신·읽음·라벨·보관·휴지통·삭제는 이 워크플로에 포함하지 않습니다. 선택을 바꾼 뒤 `job-hunt mail-check`를 실행하고 크론 등록기를 다시 실행해 로드할 스킬을 반영합니다.

## 모델

`config/models.yaml`의 공개 경로는 선택 사항입니다. 미설정이면 Python 수집만 수행합니다. 공개 분석에는 공개 공고 필드만 전송합니다. 개인 판정용 모델·provider를 지정하기 전에는 크론 활성화를 거부합니다. 모델·채널 변경 후 크론 등록기를 다시 실행하세요.

## 자료와 문서

구직 폴더의 resume/에 master_resume.md, fit_evidence.md, role_contexts.md, portfolios.md를 채웁니다. 기존 경력 원문도 참고할 수 있지만 확인하지 않은 수치나 최신 경력을 만들지 않습니다.

```bash
job-hunt documents --company "지원회사" --position "직무" \
  --jd-file "/path/to/job.md" --out "/path/to/output" --lang ko --portfolio
```

`--pdf`는 로컬 Chrome/Chromium이 있을 때 사용합니다. 제출 전 페이지 나눔과 잘림을 확인하세요. 앱의 assets/ 문서 서식은 관리 코드이며 업그레이드 시 갱신되므로 변경 사항은 소스에서 관리하거나 백업에서 보존하세요.

## TaskNotes

Dashboard.base에 TaskNotes 칸반을 포함합니다. 기존 대시보드는 업그레이드로 교체하지 않습니다. 상태는 open / waiting / in-progress / done이며, 세부를 지정하면 코드가 상태를 유도합니다. 기존 기록의 일괄 삭제·정규화는 설치에 포함하지 않습니다.

## Aside

별도 Aside 스킬의 로컬 정책으로 계정·지원 범위·이력서 선택·문항 답변·제출 한도를 정합니다. 플랫폼 저장 이력서를 우선 사용합니다. 과거 지원 내역이나 확인되지 않은 제출을 반복하지 않습니다. 이 정책과 실제 이력서는 공개 레포에 추가하지 마세요.
