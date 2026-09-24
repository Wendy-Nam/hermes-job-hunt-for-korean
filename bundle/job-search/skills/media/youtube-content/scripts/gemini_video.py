#!/usr/bin/env python3
"""YouTube → Gemini 서버사이드 시청 (IP 차단 무관). 공개 영상 URL을 Gemini에 file_data로 넘겨 요약·질문답·전사를 받는다.

  gemini_video.py <youtube_url> [--prompt "질문"] [--hq|--fast] [--text-file 자막.txt] [--model ...] [--fps 1] [--lowres] [--json]

키: env GEMINI_API_KEY → 없으면 $HERMES_DATA/.env 직독(게이트웨이 재시작 전에도 동작, proxy-doctor 관례).
stdlib만 사용. 실패는 stderr JSON + exit 1 (정직 실패 — 지어내기 금지)."""
import json, os, re, sys, time, urllib.error, urllib.request

DATA = os.environ.get("HERMES_DATA", "/opt/data")
# 실측 2026-09-12 (20분 영상): flash 48s · lite 39s · lite+0.25fps+lowres 20s (토큰 131k→55k). 말 위주 영상엔 프레임이 거의 무의미.
# 기본 = flash·0.25fps·저해상도(품질 유지, 프레임만 절약). --fast = lite(더 빠르고 얇음). --hq = flash·1fps·풀해상도(화면이 중요할 때).
DEFAULT_MODEL = "gemini-3.5-flash"
FAST_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash")   # 503/429면 이 순서(자기 자신 제외)로 1회씩 — 3.8은 과부하 잦아 꼴찌
DEFAULT_PROMPT = (
    "이 영상을 보지 않은 사람이 이 글만 읽고도 대화에서 내용을 말할 수 있게, 한국어로 정리해줘. 타임스탬프 순서로 나열하지 말고 논지 중심으로:\n"
    "1) 이 영상이 말하려는 것 — 2~3문장. 핵심 주장과 그게 왜 중요한지.\n"
    "2) 핵심 포인트 — 영상 길이에 비례해 6~12개. 각 포인트는 완결된 문장으로 '주장 → 근거(수치·사례·인용) → 함의'가 드러나게 쓰고, "
    "문장 끝에 근거 위치를 [mm:ss]로. 단어 나열·전보체 금지.\n"
    "3) 화자가 인정한 한계·반론·불확실한 점 (없으면 '언급 없음').\n"
    "4) 기억할 문장 — 화자의 실제 표현 2~3개 인용 [mm:ss].\n"
    "5) 누가 보면 좋은가 — 한 줄.\n"
    "영상에 없는 내용은 만들지 말고, 안 들리거나 애매하면 '불명확'이라고 표시해. 전체 2,000~3,500자.")
_YT = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)[A-Za-z0-9_-]{11}")


def _key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    try:
        for ln in open(os.path.join(DATA, ".env"), encoding="utf-8"):
            if ln.startswith("GEMINI_API_KEY="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _die(msg, code=1, **extra):
    print(json.dumps({"error": msg, **extra}, ensure_ascii=False), file=sys.stderr)
    sys.exit(code)


def main(argv):
    if not argv or argv[0].startswith("-"):
        _die("usage: gemini_video.py <youtube_url> [--prompt ...] [--model ...] [--json]", 2)
    url, prompt, model, as_json, fps, lowres, text_file, think = argv[0], DEFAULT_PROMPT, DEFAULT_MODEL, False, 0.25, True, None, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--prompt" and i + 1 < len(argv): prompt = argv[i + 1]; i += 2
        elif a == "--model" and i + 1 < len(argv): model = argv[i + 1]; i += 2
        elif a == "--json": as_json = True; i += 1
        elif a == "--fps" and i + 1 < len(argv): fps = float(argv[i + 1]); i += 2      # 기본 1fps. 말 위주 영상은 0.25면 충분
        elif a == "--lowres": lowres = True; i += 1                                   # 프레임당 토큰 258→66
        elif a == "--hq": model, fps, lowres = DEFAULT_MODEL, None, False; i += 1      # 화면 중요: flash·1fps·풀해상도
        elif a == "--fast": model = FAST_MODEL; i += 1                                  # 속도 우선: lite
        elif a == "--text-file" and i + 1 < len(argv): text_file = argv[i + 1]; i += 2   # 자막 원문 파일을 요약(영상 안 봄, 수 초)
        elif a == "--think": think = True; i += 1                                      # 사고 켜기(기본 off: 3배 빠르고 MALFORMED_RESPONSE 회피)
        else: _die(f"unknown arg {a}", 2)
    url = url.split("&")[0] if "watch?v=" in url else url.split("?")[0]   # 재생목록·트래킹 파라미터 제거
    if not _YT.match(url):
        _die("YouTube URL만 허용 (youtube.com/watch?v=, /shorts/, /live/, youtu.be/)", 2)
    key = _key()
    if not key:
        _die("GEMINI_API_KEY 없음 — data/.env에 추가", 3)
    gen = {"temperature": 0.3}
    if not think: gen["thinkingConfig"] = {"thinkingBudget": 0}   # 실측: 텍스트 16s→6s, 사고 4.8k토큰 후 빈 응답(MALFORMED) 회피
    if text_file:   # 자막 텍스트 모드: 영상 대신 원문을 붙인다 (타임스탬프 [mm:ss]가 줄마다 있어 인용 가능)
        try: tx = open(text_file, encoding="utf-8").read()[:400_000]
        except OSError as e: _die(f"자막 파일 못 읽음: {e}", 2)
        parts = [{"text": prompt + "\n\n=== 아래는 이 영상의 자막 원문(자동생성이라 오탈자 있음, 줄 앞 [mm:ss]는 시각) ===\n" + tx}]
    else:
        vid = {"file_data": {"file_uri": url}}
        if fps: vid["video_metadata"] = {"fps": fps}
        if lowres: gen["media_resolution"] = "MEDIA_RESOLUTION_LOW"
        parts = [vid, {"text": prompt}]
    body = {"contents": [{"parts": parts}], "generationConfig": gen}
    chain = [model] + [m for m in FALLBACK_MODELS if m != model]
    d = None; retried = {}
    i = -1
    while i + 1 < len(chain):
        i += 1; m = chain[i]
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=540) as r:   # 긴 영상은 분 단위 — 터미널 도구는 timeout=600
                d = json.load(r)
            cand = (d.get("candidates") or [{}])[0]
            if not "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])):
                # 빈 응답(MALFORMED_RESPONSE 등): 같은 모델 1회 재시도 후 다음 모델
                print(f"[{m}: 빈 응답 finish={cand.get('finishReason')} → 재시도]", file=sys.stderr)
                if not retried.get(m + ":empty"): retried[m + ":empty"] = True; chain.insert(i + 1, m)
                continue
            model = m; break
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:600]
            if e.code == 400 and "thinking" in raw.lower() and "thinkingConfig" in gen:   # 이 모델은 사고 off 불가 → 켜고 재시도
                gen.pop("thinkingConfig"); body["generationConfig"] = gen; chain.insert(i + 1, m); continue
            if e.code == 503 and not retried.get(m):        # 과부하는 몇 초 뒤 풀리는 경우가 많음 — 같은 모델 1회 재시도 후 폴백
                retried[m] = True; time.sleep(4); chain.insert(i + 1, m); continue
            if e.code in (429, 503) and i + 1 < len(chain):
                print(f"[{m}: HTTP {e.code} → {chain[i+1]}로 재시도]", file=sys.stderr); continue
            hint = {429: "무료 쿼터 초과 — 잠시 후 재시도", 503: "일시 과부하 — 잠시 후 재시도", 400: "영상이 비공개/연령제한/삭제거나 URL 형식 문제",
                    403: "키 권한/프로젝트 문제", 404: "모델명 확인(--model)"}.get(e.code, "")
            _die(f"HTTP {e.code} {hint}".strip(), model=m, detail=raw)
        except Exception as e:
            _die(f"{type(e).__name__}: {e}", model=m)
    try:
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        _die("응답에 텍스트 없음(차단/안전필터 가능)", model=model, detail=json.dumps(d, ensure_ascii=False)[:600])
    usage = d.get("usageMetadata", {})
    if as_json:
        print(json.dumps({"url": url, "model": model, "text": text, "usage": usage}, ensure_ascii=False))
    else:
        print(text.strip())
        print(f"\n[{model} · tokens in={usage.get('promptTokenCount')} out={usage.get('candidatesTokenCount')}]", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
