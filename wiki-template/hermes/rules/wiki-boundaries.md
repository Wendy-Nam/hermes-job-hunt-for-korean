---
title: Wiki boundaries
type: policy
tags: [wiki, privacy, retrieval, cron]
private: true
sot: true
---

# 위키 경계 정책

## 볼트별 읽기·쓰기
메인 위키(`$WIKI_PATH` 또는 `/opt/data/wiki`)는 default/work 프로필이 업무 및 일상 기록 범위에서 읽고 쓴다. public 프로필은 위키 내부의 개인정보, 인증정보를 읽거나 쓰지 않는다.

## 폴더 보존 규칙
- `assets/` 또는 첨부 폴더: 원본과 개인정보 첨부. 기본 검색·외부 모델 입력에서 제외한다.
- `outputs/`: 사람이 확인할 최종 산출물.
- `_archive/`: 완료·대체 문서. 복구를 위해 보존한다.
- `tmp/`, backup, 대형 export, binary와 실행 출력은 기본 검색에서 제외한다.

물리 이동·삭제는 별도 승인과 링크 검증 후 수행한다. 기존 파일 전체 frontmatter 재작성은 금지한다.

## 외부 모델 게이트
`private: true`는 외부 전송 허가가 아니다. 외부 모델 입력은 명시적 public allowlist와 승인된 목적이 모두 있어야 한다. 개인정보, 인증정보, 원문 구직 공고, 사적 대화 기록은 public 또는 무료 위임에 보내지 않는다.

## 로그와 실행 증거
주요 업무 변경 요약은 위키 `log.md`에 남긴다. `/opt/data/cron/jobs.json`의 `last_run`과 `last_status`만으로 성공을 주장하지 않는다. 구조화된 실행 증거가 있을 때만 관측 성공으로 간주한다.
