# 운영 경로

- 앱: `@APP@`
- 정본 자료: `@JOB_ROOT@`
- 상태·장부·잠금: `@STATE@`
- 경로 계약: `@APP@/config/paths.yaml`
- 구직 전용 쓰기 스위치: `@STATE@/.kit-live`

`job-hunt status`에서 실제 경로와 쓰기 허용 상태를 확인한다. `HERMES_KIT_DRY_RUN=1 job-hunt collect`는 한 번의 실행에서 노트·장부 쓰기를 차단한다. 기존 `/opt/data/.kit-live`와 `HERMES_KIT_LIVE=1`은 새 구직 앱에 쓰기 권한을 주지 않는다.

수집기는 기존 지원 노트를 자동 삭제·이동·정규화하지 않는다. 자료 정리는 별도 요청에서 수행한다. 정기 수집에는 별도 LLM이 필요 없다. 판정 잡은 비어 있지 않은 대기열의 변화가 있을 때만 실행한다. 회사 확인이나 JD 확보가 안 된 항목은 임의로 통과시키지 않고 대기열에 남긴다.

검색 프로필의 `track_bonus`는 AI 교육 90, 자동화 실무·PM 80, GTM·RevOps 70이다. 회사 티어와 적합도 가산점보다 트랙 간 차이가 크므로 직무 우선순위가 먼저 적용된다. 수치는 검색 프로필에서만 바꾼다.

정기 실행의 `job-hunt harvest`는 Python 수집 뒤 `public-review`를 실행한다. 무료 모델에는 허용된 공개 공고 필드만 전달하고 Hermes 메모리·이력서·개인 조건은 로드하지 않는다. 무료 분석은 `@STATE@/public-reviews.json`에 쌓인다. 모델 경로는 `@APP@/config/models.yaml`에서 관리하며 무료 단계 실패를 유료 풀로 자동 대체하지 않는다.
