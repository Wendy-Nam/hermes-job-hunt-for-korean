#!/usr/bin/env python3
"""유튜브 진입점. 기본 순서 = 가장 빠른 것부터:
  ① Gemini가 영상을 직접 봄(요약·질문답, 3분 영상 ~20초·24분 ~30초)
  ② 실패(503 체인 소진·비공개/연령제한·키 없음)면 Apify 자막 액터(무료 크레딧 $0.005) → 자막을 Gemini 텍스트 모드로 정리, 그것도 안 되면 자막 원문 출력
  ⓪ residential 프록시(WEBSHARE_PROXY_USERNAME / HERMES_SCRAPER_PROXY)가 있을 때만 맨 앞에 자막 API 1회(3초, 정확 인용)
  yt.py <url> [--prompt "..."] [--raw] [--apify-first] [--no-apify] [--try-direct] [--fast|--hq|--model ...]
--raw: 정리 대신 자막 원문(Apify/자막API) — 정확한 인용용. 자막은 항상 /opt/data/tmp/yt-<id>.txt에도 저장."""
import json, os, re, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("HERMES_DATA", "/opt/data")
ACTOR = "starvibe~youtube-video-transcript"     # 원어 자막 + 메타데이터, $0.005/영상 (2026-09-12 실측 19s)
APIFY_EST_USD = 0.005
url, rest = sys.argv[1], sys.argv[2:]
FLAGS = {"--raw", "--apify-first", "--no-apify", "--try-direct"}
flags = {a for a in rest if a in FLAGS}
rest = [a for a in rest if a not in FLAGS]          # 나머지는 gemini_video.py로 그대로 전달
GEM = [sys.executable, os.path.join(HERE, "gemini_video.py"), url]


def _vid(u):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", u); return m.group(1) if m else "video"


def _env(key):
    v = os.environ.get(key, "").strip()
    if v: return v
    try:
        for ln in open(os.path.join(DATA, ".env"), encoding="utf-8"):
            if ln.startswith(key + "="): return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError: pass
    return ""


def _residential():
    return bool(_env("WEBSHARE_PROXY_USERNAME") or _env("HERMES_SCRAPER_PROXY"))


def gemini_video():
    """① 영상 직접 시청. 성공하면 종료(출력은 gemini_video.py가 직접 찍음)."""
    return subprocess.call(GEM + rest) == 0


def transcript_api():
    """⓪ youtube-transcript-api (proxynet 경유). 성공 시 텍스트, 아니면 None."""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "fetch_transcript.py"), url, "--text-only", "--timestamps"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and len(r.stdout.strip()) > 200 and '"error"' not in r.stdout[:200]:
            return "[transcript]\n" + r.stdout.strip()
    except Exception:
        pass
    return None


def apify_transcript():
    """② Apify 액터. 월 예산은 구직 번들 전용 장부(state/apify-spend.json). (텍스트, 실패사유)"""
    tok = _env("APIFY_TOKEN")
    if not tok:
        try: tok = open(os.path.join(DATA, ".apify-api-token"), encoding="utf-8").read().strip()
        except OSError: return None, "APIFY_TOKEN 없음"
    try:
        import music_apify_budget as B
    except Exception as e:
        return None, f"예산 장부 모듈 없음({type(e).__name__}) — 무계량 실행 금지"
    if not B.reserve(APIFY_EST_USD):
        return None, f"월 예산 소진(${B.snapshot().spent_usd:.2f}/{B.monthly_cap_usd():.2f})"
    req = urllib.request.Request(
        f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={tok}&timeout=120&format=json",
        data=json.dumps({"youtube_url": url, "include_transcript_text": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        items = json.load(urllib.request.urlopen(req, timeout=150))
    except Exception as e:
        B.settle(0.0, APIFY_EST_USD)       # 실패한 런은 과금 안 됨 → 환불
        return None, f"apify {type(e).__name__}: {str(e)[:80]}"
    if not items or not items[0].get("transcript"):
        return None, "apify: 자막 없음(자막 꺼진 영상)"
    it = items[0]
    head = (f"[transcript via apify] {it.get('title','')} · {it.get('channel_name','')} · "
            f"{int(it.get('duration_seconds') or 0)//60}분 · lang={it.get('language') or it.get('transcript_language') or '?'}")
    return "\n".join([head] + [f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text']}"
                              for s in it["transcript"] if s.get("text")]), None


def deliver(text, source):
    """자막 확보 후: 파일 저장 → (--raw면 원문) → Gemini 텍스트 정리 → 그것도 실패면 원문."""
    path = os.path.join(DATA, "tmp", f"yt-{_vid(url)}.txt"); os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(text)
    note = f"[자막 원문({source}, {text.count(chr(10))+1}줄): {path} — 정확한 인용은 grep/read_file]"
    if "--raw" in flags or subprocess.call(GEM + ["--text-file", path] + rest) != 0:
        print(text)
    print("\n" + note)
    return 0


if "--try-direct" in flags or _residential():
    t = transcript_api()
    if t: sys.exit(deliver(t, "youtube-transcript-api"))
    print("[자막 API 실패]", file=sys.stderr)
if "--apify-first" not in flags and "--raw" not in flags:
    if gemini_video(): sys.exit(0)
    print("[gemini 영상 모드 실패 → apify]", file=sys.stderr)
if "--no-apify" not in flags:
    t, why = apify_transcript()
    if t: sys.exit(deliver(t, "apify"))
    print(f"[apify 불가: {why}]", file=sys.stderr)
    if "--apify-first" in flags or "--raw" in flags:
        sys.exit(0 if gemini_video() else 1)
sys.exit(1)
