#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""외국계 ATS 직접 조회 (Greenhouse/Lever/Ashby 공개 API, 인증 불필요).

국내 외국계는 채용을 서구 ATS에 올림 → 링크드인보다 완전하고 직접 지원 URL 제공.
대상 회사는 targets JSON에서 관리(사용자가 확장). 기본 Korea 지역 필터.

Usage:
    python3 ats_search.py --keyword sales --limit 40
    python3 ats_search.py --keyword "business development" --json
    python3 ats_search.py --company coupang --keyword 영업 --all-locations
targets: /opt/data/skills/job-search/ats-search/data/ats-targets.json
"""
from pathlib import Path
import argparse, json, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

import re
KST = timezone(timedelta(hours=9))
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
TARGETS = str(Path(__file__).resolve().parent.parent / "data" / "ats-targets.json")  # 스킬과 함께 이동
# 저연차 주니어 대상 → 시니어 제목 제외 (기본 on, --all-levels로 해제)
# 시니어 필터는 공유 jobfilter(search-profile.yaml 단일 SoT)에서 가져온다. 실패 시 폴백.
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__)))))
try:
    from jobfilter import is_senior
except Exception:
    _SENIOR = re.compile(r"(?i)(senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b|chief|高[级級]|리더|리더급|strategic|시니어|수석|책임|총괄|팀장|실장|본부장|팀리드|파트장|그룹장|\bintern(ship)?\b|인턴|체험형)")
    def is_senior(title):
        return bool(_SENIOR.search(title or ""))
KR_HINTS = ("korea", "seoul", "서울", "한국", "대한민국", "gyeonggi", "경기", "부산", "busan", "incheon", "판교", "pangyo")


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read(MAX_BYTES))   # 상한 적용(선언만 하고 안 쓰던 버그)


def greenhouse(token):
    js = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs").get("jobs", [])
    return [dict(title=j.get("title", ""), location=(j.get("location") or {}).get("name", ""),
                url=j.get("absolute_url", ""), id=str(j.get("id", ""))) for j in js]


def lever(token):
    js = get(f"https://api.lever.co/v0/postings/{token}?mode=json")
    return [dict(title=j.get("text", ""), location=(j.get("categories") or {}).get("location", ""),
                url=j.get("hostedUrl", ""), id=j.get("id", "")) for j in js if isinstance(j, dict)]


def ashby(token):
    js = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}").get("jobs", [])
    return [dict(title=j.get("title", ""), location=j.get("location", ""),
                url=j.get("jobUrl") or j.get("applyUrl", ""), id=j.get("id", "")) for j in js]


FETCH = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def load_targets(company=None):
    try:
        tg = json.load(open(TARGETS, encoding="utf-8"))
    except FileNotFoundError:
        print(f"경고: {TARGETS} 없음. 대상 회사를 추가하세요.", file=sys.stderr); return []
    return [t for t in tg if not company or t.get("token") == company or t.get("company", "").lower() == company.lower()]


def scan(company, keyword, korea_only, limit, junior_only=True):
    out = []
    for t in load_targets(company):
        fn = FETCH.get(t.get("ats"))
        if not fn:
            continue
        try:
            jobs = fn(t["token"])
        except Exception as e:
            print(f"경고: {t.get('company')} ({t.get('ats')}) 조회 실패 — {type(e).__name__}", file=sys.stderr)
            continue
        for j in jobs:
            loc = (j.get("location") or "").lower()
            if korea_only and not any(h in loc for h in KR_HINTS):
                continue
            if keyword and keyword.lower() not in j.get("title", "").lower():
                continue
            if junior_only and is_senior(j.get("title", "")):   # 저연차 주니어 → 시니어 제목 제외
                continue
            j["company"] = t.get("company", t["token"])
            out.append(j)
        time.sleep(0.4)
    return out[:limit]


def to_rows(jobs):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [f"| {j['company']} | {j['title']} | ats | | 발견 | {today} | {j['url']} |" for j in jobs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="")
    ap.add_argument("--company", default="")
    ap.add_argument("--all-locations", action="store_true")
    ap.add_argument("--all-levels", action="store_true", help="시니어 포함(기본은 주니어만)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()
    jobs = scan(a.company or None, a.keyword, not a.all_locations, a.limit, junior_only=not a.all_levels)
    if a.json:
        print(json.dumps(jobs, ensure_ascii=False, indent=2)); return 0
    if a.table:
        print("| 회사 | 포지션 | 보드 | 적합도 | 상태 | 갱신일 | 공고링크 |\n|---|---|---|---|---|---|---|")
        print("\n".join(to_rows(jobs))); return 0
    print(f'# 외국계 ATS "{a.keyword or "전체"}" — {len(jobs)}건\n')
    for j in jobs:
        print(f"- **{j['title']}** · {j['company']} · {j['location']}\n  {j['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
