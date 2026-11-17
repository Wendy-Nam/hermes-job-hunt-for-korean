# Aside 실행 환경

이 스킬은 macOS 또는 Windows의 Aside 브라우저에서 사용한다. Linux VPS의 Hermes에 Aside 브라우저를 설치하는 방식이 아니다. Hermes는 수집·판정·자료 관리를 맡고 Aside는 사용자의 로그인 세션에서 지원을 실행한다.

Aside의 사용자 스킬 설치 기능에 이 폴더를 등록한다. 단일 SKILL.md만 전달했다면 references·assets·scripts도 같은 폴더 구조로 함께 설치한다. 설치 기능과 데이터 폴더는 버전에 따라 달라질 수 있으므로 현재 Aside의 스킬 관리 화면을 따른다. 설치 뒤 `aside-job-apply 스킬을 사용할 수 있는지 확인해줘`로 인식을 확인한다.

외부 에이전트와 연결할 때는 공식 CLI/MCP를 사용한다. `aside mcp`는 로컬 표준입출력 서버다. 다운로드 API를 작업 실행 API로 쓰거나 비공개 세션 REST 주소를 추측하지 않는다. CLI 계정 인증과 기기 승인은 사용자가 완료한다.

작업 폴더에는 정책과 제출 이력을 두고 필요한 폴더만 Aside에 허용한다. 플랫폼 로그인은 Aside의 기존 프로필과 Password Manager를 사용한다. 비밀번호나 쿠키를 스킬 파일에 넣지 않는다. 실제 제출 전 계정과 승인 정책을 확인한다.

공식 문서:
- https://aside.com/developers
- https://docs.aside.com/help/tasks
- https://docs.aside.com/help/password-manager
- https://docs.aside.com/help/security

실제 플랫폼 UI·로그인·최종 접수는 이 배포 코드의 오프라인 테스트가 검증하지 않는다. 처음에는 작성 모드로 대상 플랫폼에서 동작을 확인한 뒤 승인된 자동 제출 정책을 사용한다.
