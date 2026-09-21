---
title: Hermes 상세 라우팅 규칙
document_type: policy
sot: true
status: active
---

# 프로필·도구 라우팅 상세 규칙

## 프로필
프로필 목록은 live 환경의 `hermes profile list`로 현재 존재하는 프로필과 그 역할 설명을, `hermes profile show <name>`으로 개별 프로필의 live 모델·프로바이더를 확인한다. 각 프로필의 역할은 `hermes profile describe <name>`(또는 해당 프로필 SOUL.md)이 SoT다.

공통 원칙:
- `public` 프로필은 공개 URL 수집과 evidence envelope만 처리한다. 내부 위키와 개인정보를 읽거나 쓰지 않는다.
- 생활·건강·루틴·저널을 다루는 프로필은 외부 전송을 기본 금지한다.
- 활성 식별자 외의 존재하지 않는 프로필 이름은 사용하지 않는다.

## 개인정보와 승인
메일, 캘린더, 건강, 관계, 인증정보와 이력서 원문은 public 또는 무료 위임으로 보내지 않는다. 내부 기록은 지정된 볼트/위키에만 쓴다. 외부 게시, 메일, 메시지, 외부 API 쓰기, 설정·크론·보안 경계 변경, 대량 이동·삭제는 초안과 승인 후 실행한다.

## 웹수집
Crawl4AI 로컬 실행을 우선한다. 단순 텍스트는 `web_extract`를 사용한다. 실패한 수집은 envelope으로 만들지 않으며 마지막 캐시를 새 사실로 사용하지 않는다.

## 복합 작업
`public 수집 → research 분석 → creator 산출물 → default 최종 전달` 순으로 단계를 분리한다. 각 단계는 source boundary, privacy, evidence_required를 가진다. 

## 칸반과 크론
실제 크론 선언은 `/opt/data/cron/jobs.json`이며 CLI(`hermes cron edit` 등)를 통해 조작한다. script-only 감시는 local/default로 유지하고 외부 전송하지 않는다.
