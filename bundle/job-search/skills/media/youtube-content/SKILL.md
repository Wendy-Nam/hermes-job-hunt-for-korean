---
name: youtube-content
description: "YouTube transcripts to summaries, threads, blogs."
platforms: [linux, macos, windows]
---

# YouTube Content Tool

`SKILL_DIR` = 이 SKILL.md가 있는 디렉터리. 스크립트는 둘 다 stdlib(전사 스크립트만 `uv run`으로 youtube-transcript-api 자동 설치).

## 경로 — `yt.py` 하나로 (2026-09-12 확정)

```bash
python3 SKILL_DIR/scripts/yt.py "URL"                                   # ① Gemini가 영상을 직접 봄(3분 12초·24분 27초) → 실패 시 ② Apify 자막(무료 크레딧)→Gemini 텍스트 정리 → 그것도 실패면 자막 원문
python3 SKILL_DIR/scripts/yt.py "URL" --prompt "질문 / 챕터 / 스레드 형식"   # 정리 형식·질문 (기본: 논지 중심 정리)
python3 SKILL_DIR/scripts/yt.py "URL" --raw                              # Apify 자막 원문(타임스탬프)만 — 정확한 인용용
python3 SKILL_DIR/scripts/yt.py "URL" --apify-first                      # 자막 기반 정리를 우선(발화 인용 정밀)
python3 SKILL_DIR/scripts/yt.py "URL" --hq                               # 화면·슬라이드가 중요할 때(1fps·풀해상도, 2배 느림)
```

- **Gemini 출력은 완성본**: 구조·문장 그대로 전달(긴 건 여러 메시지로 나뉘어도 됨). 압축·전보체 금지. 네 말은 앞뒤 한 줄씩만.
- 자막이 확보되면 `/opt/data/tmp/yt-<id>.txt`에 저장된다(출력 끝에 경로). 정확한 문장이 필요하면 `grep`/`read_file`로 **부분만** 읽어라 — 도구 출력 6KB 상한이라 통째로는 못 읽는다.
- 전부 실패해서 네가 자막 파일로 직접 정리하게 되면(드묾): 아래 '설명 방식' + **디스코드 가독성**(포인트마다 굵은 제목 한 줄 → 본문 2~3문장 → 빈 줄, 한 덩어리 벽글 금지).
- Apify 예산: 구직 번들 전용 장부(`state/apify-spend.json`, 월 상한 `HERMES_APIFY_MONTHLY_USD` 기본 $5, 편당 $0.005). 키: `data/.env`의 `GEMINI_API_KEY`·`APIFY_TOKEN`(스크립트가 직접 읽음, 재시작 불필요).
- residential 프록시(`WEBSHARE_PROXY_USERNAME/PASSWORD`)가 있으면 yt.py가 자막 API를 맨 앞에 시도한다(3초). 데이터센터 프록시·VPS 직접은 유튜브가 차단하므로 시도하지 않는다. 반복 재시도 금지, yt-dlp·쿠키·브라우저 스크래핑 금지. 전부 실패하면 정직하게 보고.

## When to use

유저가 유튜브 URL을 주거나, 영상 요약·자막·챕터·스레드·블로그 변환을 요청할 때.

## Output Formats

- **Summary**(기본): 요지 1줄 + 핵심 5~8개(타임스탬프) + 인용 + 대상
- **Chapters**: 주제 전환 기준 타임스탬프 목록
- **Thread**: X 스레드(각 280자 이하)
- **Blog post**: 제목·섹션·핵심 정리
- **Quotes**: 타임스탬프 달린 인용

원하는 형식은 `--prompt`로 그대로 요청하면 Gemini가 영상을 보고 맞춰 준다.

## 설명 방식 (자막을 네가 직접 정리할 때)

완결된 문장으로. 단어 나열·전보체·화살표 축약 금지. 각 포인트는 '주장 → 근거(수치·사례·인용) → 함의'가 드러나게, 근거 위치는 [mm:ss]. 처음 나오는 용어·고유명사는 한 번 풀어 쓰기. 영상 안 본 사람이 읽고 대화에서 말할 수 있는 수준이 기준 — 짧게가 아니라 이해되게.

## Workflow

1. `yt.py`로 가져온다(형식 요청은 `--prompt`).
2. 결과를 검증한다 — 영상에 없는 내용이 섞이지 않았는지, 타임스탬프가 그럴듯한지. 의심되면 `--prompt "N분 구간에서 실제로 뭐라고 말했는지 인용"`으로 재확인.
3. 유저 요청 형식으로 다듬어 전달. 출력에 "[모델 · tokens]" 꼬리(stderr)는 붙이지 않는다.

## Error Handling

- **HTTP 400**: 비공개·연령제한·삭제 영상 또는 URL 형식 — 유저에게 URL 확인 요청.
- **HTTP 429**: 무료 쿼터(분당/일일) — 잠시 후 재시도 또는 `--model gemini-3.5-flash-lite`.
- **키 없음(exit 3)**: `data/.env`에 `GEMINI_API_KEY=` 추가 요청.
- **`[transcript unavailable → apify]` / `[apify 불가: … → gemini]`**(stderr): 정상적인 폴백 진행. 재시도하지 않는다.
- **Apify 예산 소진**: 다음 달 리셋(무료 $5). 그동안은 ③ Gemini 영상 모드로 자동.
