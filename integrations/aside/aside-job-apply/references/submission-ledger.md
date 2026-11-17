# 제출 이력

Python 3.10 이상과 표준 sqlite3만 필요하다. 전용 작업 폴더에서 `state/applications.sqlite`를 사용하고 정책·이력·이력서를 공개 저장소에 넣지 않는다. Python 실행이나 로컬 파일 쓰기가 불가능하면 폼 작성까지만 진행하고 자동 제출은 멈춘다.

```bash
python scripts/application_ledger.py --db state/applications.sqlite check --url "공고URL"
python scripts/application_ledger.py --db state/applications.sqlite reserve --policy application-policy.json --url "공고URL" --resume-id "플랫폼 이력서 ID" --account "승인 계정 식별자"
python scripts/application_ledger.py --db state/applications.sqlite finalize --ticket "예약 티켓" --status submitted --evidence "접수번호 또는 확인 화면 근거"
```

`reserve`는 최종 클릭 바로 전에 사용한다. 승인·기한·도메인·계정·공고 범위·횟수를 검사하고 티켓을 만든다. 이미 등록된 공고는 새 정책이나 다른 이력서로도 다시 예약할 수 없다. `intent`와 `unknown`도 한도를 차지한다. 이 스크립트는 브라우저를 조작하거나 직무 적합도를 판단하지 않는다. JD·이력서·폼의 실제 조건 확인은 스킬의 책임이다.

제출 후 확인되지 않으면 `finalize --status unknown --evidence "제출 후 타임아웃"`을 기록한다. 기록을 지우거나 새 티켓으로 재시도하지 않는다. 접수 증거가 나중에 확인되면 같은 티켓을 `submitted`로 확정할 수 있다.

Aside의 필수 승인 대기로 실제 클릭 전 멈췄다면 현재 작업의 같은 티켓과 화면 상태를 유지한다. 새 작업에서 기존 intent를 발견하면 제출 여부가 불확실하므로 자동으로 이어 누르지 않는다.
