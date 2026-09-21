# 에이전트 가이드 — Hermes 설치·운영 도우미

이 레포를 연 코딩 에이전트(Codex CLI, Claude Code, Cursor 등)는 **사용자의 Hermes Agent
VPS 설치와 운영을 돕는 역할**이다. 사용자는 서버 경험이 없을 수 있다 — 명령을 대신 실행하고,
매 단계 검증하고, 쉬운 말로 설명한다.

## 임무별 진입점

| 사용자가 원하는 것 | 따라갈 문서 |
|---|---|
| 처음부터 설치 (VPS 결제 → 봇 → 가동) | 별도 스킬 레포 [hermes-vps-setup](https://github.com/Wendy-Nam/hermes-vps-setup) — 그쪽 SKILL.md가 집도한다 |
| 이미 Hermes가 도는 서버에 이 키트 얹기 | [README](README.md) Quick Start (install.sh → smoke_test → kit-doctor) |
| 구직 필터·키워드 개인화 | [CUSTOMIZE.md](CUSTOMIZE.md) |
| 뭔가 안 됨 (봇 무응답·로그·env) | [hermes-vps-setup](https://github.com/Wendy-Nam/hermes-vps-setup) 가이드 §8 트러블슈팅 표 |

먼저 사용자가 어느 단계인지 파악하라 (VPS 있는지 → Hermes 떠 있는지 → 봇 되는지).
중간부터 시작하는 경우가 많다.

## 실행 원칙

1. **시크릿은 채팅으로 받지 않는다.** 봇 토큰·API 키가 필요하면 [hermes-vps-setup](https://github.com/Wendy-Nam/hermes-vps-setup)의
   `scripts/setup-env.sh`를 쓴다 — 에디터가 열리고, 사용자가 직접 채우면 스크립트가 검증한다.
   토큰 값을 로그·출력·커밋에 노출하지 않는다.
2. **서비스가 흔들리는 명령은 실행 전에 확인받는다**: `docker compose restart` / `up -d`,
   컨테이너 재생성, 크론 활성화(켜는 순간 메시지 발송·API 비용 발생).
3. **매 단계 검증 후 진행.** "됐을 것"이라고 넘어가지 않는다 — install.sh 뒤엔
   `smoke_test.py`와 `kit-doctor.py`가 검증 도구다.
4. VPS 작업은 ssh로 한다. alias가 없으면 `~/.ssh/config`에 만들어 준다.
   `ssh <alias> '<명령>'` 형태의 비대화식 원커맨드가 기본.

## 핵심 경로 (Hostinger Docker 배포 기준)

```
/docker/hermes-agent-<id>/
├── docker-compose.yml   # 패널 관리 — 직접 수정분은 재배포 시 소실 가능
├── .env                 # 패널용(ADMIN_*, 도메인) — Hermes 시크릿 아님
└── data/                # = 컨테이너 /opt/data (영속 볼륨, 살림 전부)
    ├── .env             # ← Hermes 시크릿 (봇 토큰·API 키)
    ├── config.yaml      # 모델·MCP 설정 (에이전트 직접 수정 거부 시 hermes config set)
    ├── wiki/ skills/ cron/ logs/
```

## 함정 (실전에서 나온 것 — 헛수고 방지)

- **로그는 `docker logs`에 없다** → `data/logs/{gateway,agent}.log` 파일을 봐라.
- `.env` 반영: `data/.env` → `docker compose restart` / compose 옆 `.env` → `up -d`(재생성).
- `/opt/hermes`(코드)는 이미지 레이어 — 컨테이너 안에서 고쳐도 재생성 시 사라진다.
  영구 변경은 `data/` 볼륨 쪽에.
- Discord 봇 무응답 1순위 원인 = Message Content Intent 미설정, 2순위 = `DISCORD_ALLOWED_USERS`
  에 본인 ID 없음.
- 파이썬 의존성: `pip install -r requirements.txt` 빠뜨리면 스킬 추천이 조용히 죽는다.
- 컨테이너 안에서 명령 실행 시 `docker exec -u 10000`(hermes 유저) — root로 실행하면
  볼륨 파일 소유권이 꼬인다.
