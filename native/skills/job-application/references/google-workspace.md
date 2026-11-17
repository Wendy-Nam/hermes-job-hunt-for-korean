# Google Workspace 메일 읽기

`google-workspace` 스킬을 먼저 로드한다. 그 스킬의 `scripts/setup.py --check`로 인증을 확인하고 `scripts/google_api.py`를 통해 Gmail을 검색·조회한다. gws 설치 여부와 Python 폴백은 공식 래퍼가 처리한다. 별도 로그인이나 계정 변경을 자동으로 시도하지 않는다.

표준 설치 경로의 예:

```bash
python "${HERMES_HOME}/skills/productivity/google-workspace/scripts/setup.py" --check
python "${HERMES_HOME}/skills/productivity/google-workspace/scripts/google_api.py" gmail search 'newer_than:7d {채용 지원 접수 면접 interview application offer}' --max 20
python "${HERMES_HOME}/skills/productivity/google-workspace/scripts/google_api.py" gmail get MESSAGE_ID
```

검색 전에 `@STATE@/cycle.json`의 주기 시작 시각을 확인한다. 시각을 Unix 초로 변환한 `after:<초>` 조건을 검색어에 함께 넣어 이전 구직 메일을 제외한다. 복잡한 검색은 google-workspace의 `references/gmail-search-syntax.md`, 채용 메일 분류는 `references/job-application-email-triage.md`를 참고한다.

제목·날짜·보낸 사람부터 최대 20건 확인하고, 상태나 필요한 액션이 애매한 메일만 본문을 읽는다. 목록·본문 전체를 채팅에 출력하지 않는다. 읽음·라벨·이동·발송 등의 변경 명령은 호출하지 않는다.
