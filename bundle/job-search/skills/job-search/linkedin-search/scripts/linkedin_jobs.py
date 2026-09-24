#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""링크드인 채용공고 검색 (게스트 엔드포인트, 로그인 불필요).

경력·기간·근무형태 필터 + 위키 파이프라인 테이블 자동 병합(dedup).

Usage:
    python3 linkedin_jobs.py "데이터 엔지니어" --level 신입,인턴 --since 1주 --limit 25
    python3 linkedin_jobs.py "백엔드" --level 신입 --remote --json
    # (수집은 job-collect.py가 자동으로 한다 — 이 스크립트는 조회 전용)

필터:
    --level  인턴,신입,주니어,미드      (f_E: 1/2/3/4, 콤마 다중)
    --since  24h | 1주 | 1달           (f_TPR)
    --type   정규,계약,파트,인턴,임시     (f_JT: F/C/P/I/T)
    --remote                          (f_WT=2 리모트)
    --location "South Korea"          (기본)
출력:
    (기본) 마크다운 목록 · --json 원자료 · --table 파이프라인 행
"""
import argparse, html as _html, json, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
KST = timezone(timedelta(hours=9))

LEVEL = {"인턴": "1", "신입": "2", "주니어": "3", "미드": "4", "시니어": "4", "디렉터": "5"}
JTYPE = {"정규": "F", "계약": "C", "파트": "P", "인턴": "I", "임시": "T"}
SINCE = {"24h": "r86400", "1주": "r604800", "week": "r604800", "1달": "r2592000", "month": "r2592000"}
# 저연차 주니어 대상 → 시니어 제목 백업 필터 (f_E가 놓치는 高级/Senior 등 차단). --all-levels로 해제.
# 시니어 필터는 공유 jobfilter(search-profile.yaml 단일 SoT)에서 가져온다. 실패 시 폴백.
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__)))))
try:
    from jobfilter import is_senior
except Exception:
    _SENIOR = re.compile(r"(?i)(senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b|chief|高[级級]|리더|리더급|strategic|시니어|수석|책임|총괄|팀장|실장|본부장|팀리드|파트장|그룹장|\bintern(ship)?\b|인턴|체험형)")
    def is_senior(title):
        return bool(_SENIOR.search(title or ""))


def fetch_page(kw, loc, f_E, f_TPR, f_JT, f_WT, start):
    q = {"keywords": kw, "location": loc, "start": start}
    if f_E: q["f_E"] = f_E
    if f_TPR: q["f_TPR"] = f_TPR
    if f_JT: q["f_JT"] = f_JT
    if f_WT: q["f_WT"] = f_WT
    req = urllib.request.Request(BASE + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=15).read(MAX_BYTES).decode("utf-8", "ignore")


def parse(h):
    out = []
    for c in re.split(r'<li>\s*<div class="base-card', h)[1:]:
        def g(p):
            m = re.search(p, c, re.S)
            return _html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
        url = (re.search(r'href="(https://[^"]*?/jobs/view/[^"]+?)"', c) or [None, ""])[1].split("?")[0]
        out.append({
            "title": g(r'base-search-card__title">\s*(.*?)\s*</h3>'),
            "company": g(r'base-search-card__subtitle">\s*<a[^>]*>\s*(.*?)\s*</a>') or g(r'base-search-card__subtitle">\s*(.*?)\s*</h4>'),
            "location": g(r'job-search-card__location">\s*(.*?)\s*</span>'),
            "date": g(r'datetime="([\d-]+)"'),
            "url": url,
        })
    return out


def search(kw, loc, f_E, f_TPR, f_JT, f_WT, limit):
    seen, jobs = set(), []
    start = 0
    while len(jobs) < limit and start < 100:
        try:
            page = parse(fetch_page(kw, loc, f_E, f_TPR, f_JT, f_WT, start))
        except Exception as e:
            print(f"경고: start={start} 조회 실패 ({e})", file=sys.stderr)
            break
        if not page:
            break
        for j in page:
            if j["url"] and j["url"] not in seen:   # dedup by 링크
                seen.add(j["url"]); jobs.append(j)
        start += 10
        time.sleep(1.2)   # 레이트리밋 예의
    return jobs[:limit]


def to_rows(jobs):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [f"| {j['company']} | {j['title']} | linkedin |  | 발견 | {today} | {j['url']} |" for j in jobs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--level", default="")
    ap.add_argument("--since", default="")
    ap.add_argument("--type", dest="jtype", default="")
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--all-levels", action="store_true", help="시니어 포함(기본 주니어만)")
    ap.add_argument("--location", default="South Korea")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()

    f_E = ",".join(LEVEL[x.strip()] for x in a.level.split(",") if x.strip() in LEVEL) or None
    f_JT = ",".join(JTYPE[x.strip()] for x in a.jtype.split(",") if x.strip() in JTYPE) or None
    f_TPR = SINCE.get(a.since.strip()) if a.since else None
    f_WT = "2" if a.remote else None

    jobs = search(a.query, a.location, f_E, f_TPR, f_JT, f_WT, a.limit)
    if not a.all_levels:
        jobs = [j for j in jobs if not is_senior(j.get("title", ""))]

    if a.json:
        print(json.dumps(jobs, ensure_ascii=False, indent=2)); return 0
    if a.table:
        print("| 회사 | 포지션 | 보드 | 적합도 | 상태 | 갱신일 | 공고링크 |")
        print("|---|---|---|---|---|---|---|")
        print("\n".join(to_rows(jobs))); return 0

    filt = " · ".join(f for f in [a.level, a.since, "리모트" if a.remote else "", a.jtype] if f)
    print(f'# 링크드인 "{a.query}" {("("+filt+")") if filt else ""} — {len(jobs)}건\n')
    for j in jobs:
        line = f"- **{j['title']}** · {j['company']}"
        if j["location"]: line += f" · {j['location']}"
        if j["date"]: line += f" · {j['date']}"
        print(line + f"\n  {j['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
