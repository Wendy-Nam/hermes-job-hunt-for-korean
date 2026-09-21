#!/usr/bin/env python3
"""스크래퍼 프록시 닥터 — 생존검사 + Webshare 자동교체.

죽은 프록시가 전 보드 수집을 조용히 막는 사고(2026-07 실증: 무료 프록시 수명 ~1주)를
막는다. job-collect가 런 시작에 호출하고, 사람이 직접 진단할 때도 쓴다.

동작:
  1. 후보 수집: $HERMES_DATA/.scraper-proxy 파일 → env HERMES_SCRAPER_PROXY 순
  2. 살아있는 첫 후보를 stdout에 한 줄로 출력하고 끝
  3. 전부 죽었으면: $HERMES_DATA/.env(또는 env)의 WEBSHARE_API_KEY로 Webshare에서
     프록시 목록을 받아 검증 후 .scraper-proxy에 기록(0600)하고 출력
  4. 그래도 없으면 빈 출력 = 호출자가 직접 연결로 폴백
     (죽은 프록시로 전 보드를 죽이는 것보다, 클라우드IP 차단 보드만 잃는 게 낫다)

사용:  proxy-doctor.py [--verbose]
시크릿 규율: 키·프록시 인증정보는 stderr에도 마스킹해서만 출력한다.
"""
import json
import os
import re
import sys
import urllib.request

DATA = os.environ.get("HERMES_DATA") or "/opt/data"
PFILE = os.path.join(DATA, ".scraper-proxy")
# 생존검사는 실제 차단 타깃(원티드 API)으로 — 데이터센터 IP는 원티드 WAF를 일부만 통과(2026-09-12 실측 2/10).
# google 204로 살아있어도 원티드는 403인 프록시를 고르던 사고 방지. 유튜브는 데이터센터 IP 전멸이라 이 도구 대상 아님(residential 별도).
TEST_URL = ("https://www.wanted.co.kr/api/chaos/search/v1/results?query=sales&country=kr"
            "&job_sort=job.latest_order&years=-1&locations=all&limit=1&offset=0")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
MAX_CANDIDATES = 12                                 # 무료 목록 10개 전부 — 통과 IP가 2/10뿐이라 앞 5개만 보면 놓침
VERBOSE = "--verbose" in sys.argv


def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr)


def mask(p):
    return re.sub(r"//[^@]+@", "//***@", p or "")


def alive(proxy, timeout=8):
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(urllib.request.Request(TEST_URL, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.status == 200
    except Exception as e:
        log(f"  dead: {mask(proxy)} ({type(e).__name__})")
        return False


def webshare_key():
    k = os.environ.get("WEBSHARE_API_KEY", "").strip()
    if k:
        return k
    try:  # 게이트웨이 env 주입 전이어도 .env 파일에서 직접 읽는다(재시작 불필요)
        for line in open(os.path.join(DATA, ".env"), encoding="utf-8"):
            m = re.match(r"^WEBSHARE_API_KEY=(.+)$", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def webshare_candidates(key):
    req = urllib.request.Request(
        "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=25",
        headers={"Authorization": f"Token {key}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return [f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in d.get("results", []) if p.get("valid") is not False]


def save(proxy):
    tmp = PFILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(proxy + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PFILE)


def main():
    cands = []
    try:
        cands.append(open(PFILE, encoding="utf-8").read().strip())
    except OSError:
        pass
    envp = os.environ.get("HERMES_SCRAPER_PROXY", "").strip()
    if envp:
        cands.append(envp)

    for c in [c for c in dict.fromkeys(cands) if c]:   # dedup·순서 보존
        if alive(c):
            log(f"alive: {mask(c)}")
            print(c)
            return

    key = webshare_key()
    if key:
        try:
            for c in webshare_candidates(key)[:MAX_CANDIDATES]:
                if alive(c):
                    save(c)
                    log(f"rotated → {mask(c)} (.scraper-proxy 갱신)")
                    print(c)
                    return
            log("webshare 후보 전멸")
        except Exception as e:
            log(f"webshare API 실패: {type(e).__name__}: {e}")
    else:
        log("WEBSHARE_API_KEY 없음 — 자동교체 불가 (data/.env에 추가하면 다음 런부터 자동)")

    try:  # 죽은 캐시 파일은 치워서 다음 판단을 오염시키지 않는다
        os.replace(PFILE, PFILE + ".dead")
    except OSError:
        pass
    log("프록시 없음 → 직접 연결 폴백 (클라우드IP 차단 보드[원티드]는 수집 불가)")


if __name__ == "__main__":
    main()
