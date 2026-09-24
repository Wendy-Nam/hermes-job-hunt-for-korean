#!/usr/bin/env python3
"""proxynet — 공유 프록시 리졸버.

공급 없음 전제: 핵심 역할 = 죽은 프록시 신속 감지 → 직접 연결(direct) 강등.
direct 결과도 캐시(TTL)하므로 죽은 프록시를 매 호출 재프로브하지 않는다.
소비자는 try/except로 임포트(폴트오픈): 리졸버가 없거나 죽어도 종전 동작.
"""
import json
import os
import sys
import time
import urllib.request

DATA = os.environ.get("HERMES_DATA", "/opt/data")
STATE = os.path.join(DATA, ".proxy-state.json")
CAND_FILE = os.path.join(DATA, ".scraper-proxy")
PROBE_URL = "http://www.gstatic.com/generate_204"


def _alive(proxy, timeout=3):
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        r = opener.open(PROBE_URL, timeout=timeout)
        return getattr(r, "status", None) in (200, 204) or r.getcode() in (200, 204)
    except Exception:
        return False


def _candidates():
    out = []
    try:
        for ln in open(CAND_FILE, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                out.append(ln)
    except OSError:
        pass
    env = os.environ.get("HERMES_SCRAPER_PROXY", "").strip()
    if env and env not in out:
        out.append(env)
    return out


def _webshare_key():
    key = os.environ.get("WEBSHARE_API_KEY", "").strip()
    if key:
        return key
    try:  # data/.env 직접 읽기 (proxy-doctor 관례 — 재기동 불필요)
        for ln in open(os.path.join(DATA, ".env"), encoding="utf-8"):
            if ln.startswith("WEBSHARE_API_KEY="):
                return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _webshare_replace():
    key = _webshare_key()
    if not key:
        return []
    try:
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=5",
            headers={"Authorization": "Token " + key})
        d = json.load(urllib.request.urlopen(req, timeout=10))
        cands = ["http://%s:%s@%s:%s" % (p["username"], p["password"],
                 p["proxy_address"], p["port"])
                 for p in d.get("results", []) if p.get("valid")]
        if cands:
            tmp = CAND_FILE + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(cands) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, CAND_FILE)
        return cands
    except Exception:
        return []


def resolve_proxy(ttl_sec=600):
    """생존 프록시 URL 또는 None(직접 연결). 결과는 TTL 캐시."""
    now = time.time()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
        if now - float(st.get("checked_at", 0)) < ttl_sec:
            return st.get("proxy")
    except Exception:
        if os.path.exists(STATE):
            try:
                os.replace(STATE, STATE + ".corrupt-%d" % int(now))
            except OSError:
                pass
    proxy = None
    for c in _candidates():
        if _alive(c):
            proxy = c
            break
    if proxy is None:
        for c in _webshare_replace():
            if _alive(c):
                proxy = c
                break
    if proxy is None:
        print("proxynet: 생존 프록시 없음 — 직접 연결 강등", file=sys.stderr)
    try:
        tmp = STATE + ".tmp%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"proxy": proxy, "checked_at": now}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE)
    except OSError:
        pass
    return proxy


def apply_env(proxy=None, resolve=True):
    """HTTPS_PROXY 계열 env를 리졸버 결과로 설정(direct면 제거). 스크래퍼 단독 실행용."""
    if resolve and proxy is None:
        proxy = resolve_proxy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if proxy:
            os.environ[k] = proxy
        else:
            os.environ.pop(k, None)
    return proxy


def _selftest():
    import tempfile
    global DATA, STATE, CAND_FILE
    with tempfile.TemporaryDirectory() as t:
        DATA = t
        STATE = os.path.join(t, ".proxy-state.json")
        CAND_FILE = os.path.join(t, ".scraper-proxy")
        open(CAND_FILE, "w").write("http://127.0.0.1:9\n")          # 죽은 프록시
        os.environ["HERMES_SCRAPER_PROXY"] = "http://127.0.0.1:19"  # 죽은 env
        os.environ.pop("WEBSHARE_API_KEY", None)
        t0 = time.time()
        assert resolve_proxy() is None                               # 강등
        st = json.load(open(STATE))
        assert st["proxy"] is None and st["checked_at"] >= t0        # direct 캐시
        t1 = time.time()
        assert resolve_proxy() is None
        assert time.time() - t1 < 0.5                                # 캐시 히트(재프로브 없음)
        assert apply_env() is None and "HTTPS_PROXY" not in os.environ
        open(STATE, "w").write("{broken")                            # 파손 격리
        assert resolve_proxy() is None
        assert any(f.startswith(".proxy-state.json.corrupt-") for f in os.listdir(t))
    print("selftest OK")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else print(resolve_proxy() or "direct")
