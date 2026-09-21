<!-- conditional-rules L4 module, job-hunting turns -->

### 구직 규칙 핵심 (SoT: wiki/automation/job-hunting/Rules.md)

**판정 게이트 (순서대로)**
0. 현재 지원 축에 해당하는가? — 직무 판정·최신 선호 정본은 `wiki/automation/job-hunting/Rules.md`, 선호군·검색 설정은 `search-profile.yaml`을 따른다.
1. 내가 될만한가? (자격요건·연차 부합 및 `wiki/automation/job-hunting/resume/fit_evidence.md` 적합 근거 매칭)
2. 회사 규모 및 보상 수준 적합성 확인 (필요 시 `wiki/automation/job-hunting/resume/master_resume.md` 경력 참조)
3. 명백한 비타깃(순수 단순 어드민, 비관련 도메인 등) → ❌

**이력서 덤프 & 핏 검증 소스 (SoT)**
- 사용자가 이력서/포폴/맥락 덤프 제공 시 `bin/career-dump-ingest.py`로 갱신
- 핏 판정(`--fit`) 시 `fit_evidence.md`의 정량 성과 및 `master_resume.md`의 경력 정본을 적합 사유("✅ 적합 사유") 근거로 제시

**맞춤 이력서 & 자기소개서 PDF 작성 규칙 (Truth Guard & Page Budget)**
- 맞춤 작성 명령 시 `bin/generate-tailored-resume.py` 호출
- **팩트 검증**: 검증 불가능한 거짓 정보/미경험 기술 스택 금지, `master_resume.md` 기반 성과 강조
- **분량 예산 & 리크루터 밀도**: 1~1.5장 권장 (시니어/AX 리드 최대 2장 제한), 미사여구 배제 및 KPI 중심 구문 작성

**노트 조작 규칙 (절대)**
- 생성: `bin/job-stage-commit.py`만
- 필드 갱신: `bin/note-set-field.py`만
- `write_file`·`echo`·`cat`으로 frontmatter 직접 쓰기 금지 (스키마 손상 원인)
- SoT 파일(`Rules.md`, `search-profile.yaml`, `.job-seen.json`, `fit_evidence.md`) 임의 구조 변경 금지

**상태 체계**: open{발견·준비} · waiting{지원} · in-progress{서류합격·면접·과제·오퍼} · done{탈락·면접포기·보류·마감}
