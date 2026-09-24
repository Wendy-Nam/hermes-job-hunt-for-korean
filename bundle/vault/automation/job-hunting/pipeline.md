---
title: 구직 파이프라인
type: pipeline
private: true
tags: [구직, 자동화]
---

# 구직 파이프라인

```text
보드·웹·메일 검색 → 프로필 필터·중복 제거 → 적합도 판정
→ 공고 노트 생성 → 맞춤 이력서·자소서 → 지원
→ 메일 상태 동기화 → 회사 리서치 → 면접 연습·복기
```

## 단계별 도구
1. `job-hunter-kit`: 가볍게 검색하고 주간 다이제스트를 받습니다.
2. `scripts/job-collect.py`: 여러 보드 공고와 메일 추천을 수집하고 판정 대기열을 만듭니다.
3. `job-match`와 `Rules.md`: JD를 읽고 지원·보완·제외를 판정합니다.
4. `generate-tailored-resume.py`: 마스터 경력·성과 자료에서 공고별 이력서·자소서를 만듭니다.
5. `job-alert-mail`과 메일 크론: 지원 접수·면접·탈락 상태를 안전하게 동기화합니다.
6. `company-interview-research`, `interview-grill`: 회사별 면접 준비와 복기를 합니다.

## 안전 순서
DRY-RUN 관찰 → 로컬 쓰기 허용 → 외부 메일 쓰기 허용 순서로 활성화합니다.
