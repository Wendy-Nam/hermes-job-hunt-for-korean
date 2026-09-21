#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""사람인·잡코리아 채용공고 검색 (공개 웹, 인증 불필요).

Usage:
    python3 kr_job_search.py "백엔드" --board saramin --limit 20
    python3 kr_job_search.py "데이터 분석" --board jobkorea --limit 15
    # (수집은 job-collect.py가 자동으로 한다 — 이 스크립트는 조회 전용)
"""
import argparse, html as _html, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
# 저연차 주니어 대상 → 시니어 제목 필터 (기본 on, --all-levels로 해제). 연차(N년+)는 JD 본문이라 job-match가 최종 판정.
# 시니어 필터는 공유 jobfilter(search-profile.yaml 단일 SoT)에서 가져온다. 실패 시 폴백.
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__)))))
try:
    from jobfilter import is_senior
except Exception:
    _SENIOR = re.compile(r"(?i)(senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b|chief|高[级級]|리더|리더급|strategic|시니어|수석|책임|총괄|팀장|실장|본부장|팀리드|파트장|그룹장|\bintern(ship)?\b|인턴|체험형)")
    def is_senior(title):
        return bool(_SENIOR.search(title or ""))


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=20).read(MAX_BYTES).decode("utf-8", "ignore")


def _clean(s):
    return _html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"\s+", " ", s))).strip()


def saramin(kw, limit):
    out, seen = [], set()
    page = 1
    while len(out) < limit and page <= 5:
        url = ("https://www.saramin.co.kr/zf_user/search/recruit?searchword="
               + urllib.parse.quote(kw) + f"&recruitPage={page}")
        try:
            h = _get(url)
        except Exception as e:
            print(f"경고: 사람인 page{page} 실패 ({e})", file=sys.stderr); break
        blocks = h.split('class="item_recruit"')[1:]
        if not blocks:
            break
        for b in blocks:
            rid = re.search(r'value="(\d+)"', b)
            tit = re.search(r'job_tit"[^>]*>\s*<a[^>]*title="([^"]+)"', b)
            comp = re.search(r'(?:corp_name|area_corp)"[^>]*>.*?<a[^>]*>\s*(.*?)\s*</a>', b, re.S)
            if not rid:
                continue
            rid = rid.group(1)
            if rid in seen:
                continue
            seen.add(rid)
            out.append(dict(
                board="saramin",
                title=_clean(tit.group(1)) if tit else "",
                company=_clean(comp.group(1)) if comp else "",
                location="", date="",
                url=f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={rid}",
            ))
        page += 1
        time.sleep(1.2)
    return out[:limit]


def jobkorea(kw, limit):
    url = "https://www.jobkorea.co.kr/Search/?stext=" + urllib.parse.quote(kw)
    try:
        h = _get(url)
    except Exception as e:
        print(f"경고: 잡코리아 실패 ({e})", file=sys.stderr); return []
    out, seen = [], set()
    # GI_Read 링크 기준으로 앵커 블록 추출
    for m in re.finditer(r'href="(https://www\.jobkorea\.co\.kr/Recruit/GI_Read/(\d+)[^"]*)"[^>]*(?:title="([^"]*)")?[^>]*>(.*?)</a>', h, re.S):
        gi = m.group(2)
        if gi in seen:
            continue
        title = m.group(3) or _clean(m.group(4))
        if not title or len(title) < 2:
            continue
        seen.add(gi)
        t = _clean(title)
        # 잡코리아는 React 앱이라 회사명이 별도 → 제목의 [회사명] 대괄호에서 폴백 추출
        bracket = re.match(r"\[([^\]]{2,30})\]", t)
        out.append(dict(board="jobkorea", title=t, company=bracket.group(1) if bracket else "",
                        location="", date="",
                        url=f"https://www.jobkorea.co.kr/Recruit/GI_Read/{gi}"))
        if len(out) >= limit:
            break
    return out


def to_rows(jobs):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [f"| {j['company']} | {j['title']} | {j['board']} |  | 발견 | {today} | {j['url']} |" for j in jobs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--board", default="all", choices=["saramin", "jobkorea", "all"])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all-levels", action="store_true", help="시니어 포함(기본 주니어만)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()

    jobs = []
    if a.board in ("saramin", "all"):
        jobs += saramin(a.query, a.limit)
    if a.board in ("jobkorea", "all"):
        jobs += jobkorea(a.query, a.limit)
    if not a.all_levels:
        jobs = [j for j in jobs if not is_senior(j.get("title", ""))]

    if a.json:
        import json; print(json.dumps(jobs, ensure_ascii=False, indent=2)); return 0
    if a.table:
        print("| 회사 | 포지션 | 보드 | 적합도 | 상태 | 갱신일 | 공고링크 |")
        print("|---|---|---|---|---|---|---|"); print("\n".join(to_rows(jobs))); return 0
    print(f'# "{a.query}" · {a.board} — {len(jobs)}건\n')
    for j in jobs:
        print(f"- **{j['title']}** · {j['company'] or '(회사미상)'} · [{j['board']}]\n  {j['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
