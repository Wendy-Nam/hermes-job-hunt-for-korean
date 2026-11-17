---
name: job-documents
description: 이력서 예시를 뽑거나 직무별 국문·영문 이력서, 자기소개서, 포트폴리오를 HTML/PDF로 출력할 때 사용한다. 설치된 job-hunt documents 명령과 검증된 경력 자료를 이용한다.
metadata:
  hermes:
    tags: [jobs, resume, cover-letter, portfolio]
---

# 지원 문서

출력은 설치된 `job-hunt documents`를 사용한다. `resume/variants/index.md`가 있으면 직무·언어에 맞는 자료 폴더를 선택하고 [직무별 자료·확장 서식](references/role-adaptation.md)의 `--materials-dir` 방식으로 실행한다. 없으면 아래 기본 명령을 사용한다. `work/` 아래 staging 사본을 실행하거나 생성기·jobpaths 소스를 찾아 읽지 않는다. 구체적인 공고가 없는 예시는 직무 키워드를 `--jd-text`로 전달한다.

일반 문서 출력은 원본 경력 자료·설치 코드·서식을 수정하거나 테스트 묶음을 실행하는 작업이 아니다. 오류가 나면 짧은 오류와 보존된 출력 경로를 보고한다. 사용자가 별도로 데이터 수정·개발을 요청했을 때만 그 작업을 수행한다.

근거는 `@JOB_ROOT@/resume/master_resume.md`, `fit_evidence.md`, `role_contexts.md`, `portfolios.md`와 같은 폴더의 원본 이력서·지원서다. 날짜·수치·직급을 확인하고 해당 직무의 근거를 선택한다. 마스터에 없는 사실은 원본에서 확인하되, 보강이 필요하면 알리고 원본을 임의로 바꾸지 않는다.

새 PDF·경력 자료를 반영하도록 요청받으면 [출처·충돌 관리](references/source-evidence.md)를 읽고 `source_catalog.md`, `evidence_ledger.md`, `claim_conflicts.md`를 함께 보강한다. 일반 출력에서는 정리된 자료를 재사용한다. 활동란·문체·요약형과 상세형 구성은 [확장 서식](references/role-adaptation.md)을 따른다.

AI 교육은 설명·교육자료·워크숍 운영·실습 설계 근거를 먼저, 자동화 실무·PM은 문제 정의·구현·도입·운영 근거를 먼저, GTM·RevOps는 CRM·파이프라인·데이터 품질·프로세스 근거를 먼저 배치한다. 강의 경력을 영업 경력으로 바꾸거나 교육생 수·절감 시간을 추정하지 않는다.

```bash
@APP@/job-hunt documents --company "직무별 예시" --position "AI 교육 담당자" --jd-text "AI 교육 실습 워크숍 교육자료 콘텐츠 설계" --out "@JOB_ROOT@/outputs/ai-education" --lang ko --only resume
```

실제 공고는 `--jd-text` 대신 `--jd-file "공고 노트 경로"`를 사용한다. 이력서만 요청하면 `--only resume`, 자소서만 요청하면 `--only cover-letter`, 포트폴리오만 요청하면 `--only portfolio`를 사용한다. 이력서·자소서 묶음은 `--only`를 빼고, 세 문서 묶음은 `--portfolio`를 붙인다. PDF를 요청한 경우만 `--pdf`를 붙인다. 명령은 생성한 파일 경로를 출력한다. PDF가 실패해도 HTML은 보존된다. 환경 문제를 이력서나 코드 수정으로 해결하려 하지 않는다.

생성기는 키워드로 사실을 골라 HTML 서식에 넣는다. 출력 후 경력 누락·잘못 붙은 성과·회사명·미채움 항목을 한 번 점검하고 파일을 제공한다. 전체 HTML을 여러 번 읽거나 출력 완료 뒤 표현을 바꾸려고 생성기를 수정하지 않는다. PDF 제출 전 페이지 나눔과 잘림을 점검한다.

사진·생년월일·상세 주소 등 불필요한 개인정보는 추가하지 않는다. 자료와 결과를 공개 저장소에 넣거나 외부에 보내지 않는다. 최종 제출은 사용자가 한다.
