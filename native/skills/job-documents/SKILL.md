---
name: job-documents
description: 공고별 국문·영문 이력서, 자기소개서, 포트폴리오 초안을 만들 때 사용한다. 기존 경력 자료와 검증된 성과를 이용해 문서를 렌더링한다.
metadata:
  hermes:
    tags: [jobs, resume, cover-letter, portfolio]
---

# 지원 문서

근거는 `@JOB_ROOT@/resume/master_resume.md`, `fit_evidence.md`, `role_contexts.md`, `portfolios.md`와 같은 폴더의 원본 이력서·지원서다. 원본의 날짜·수치·직급을 확인하고 해당 공고에 맞는 근거를 선택한다. 마스터에 없는 최신 사실은 원본에서 확인한 뒤 보강한다.

AI 교육은 설명·교육자료·워크숍 운영·실습 설계 근거를 먼저, 자동화 실무·PM은 문제 정의·구현·도입·운영 근거를 먼저, GTM·RevOps는 CRM·파이프라인·데이터 품질·프로세스 근거를 먼저 배치한다. 강의 경력을 영업 경력으로 바꾸거나 교육생 수·절감 시간을 추정하지 않는다.

```bash
@APP@/job-hunt documents --company "지원회사" --position "AI 교육 담당자" --jd-file "공고 노트 경로" --out "지원별 출력 폴더" --lang ko
```

생성기는 키워드로 사실을 골라 HTML 서식에 넣는 렌더러다. JD 해석과 서술 다듬기는 이 스킬에서 수행한다. HTML의 경력·연락처·공백 항목·회사명과 출력 결과를 검토한다. PDF가 필요하면 `--pdf`를 붙이고 설치된 Chrome/Chromium이 있는지 확인한다. 제출 전 PDF 페이지 나눔과 잘림을 점검한다.

사진·생년월일·상세 주소 등 불필요한 개인정보는 추가하지 않는다. 자료와 결과를 공개 저장소에 넣거나 외부에 보내지 않는다. 최종 제출은 사용자가 한다.
