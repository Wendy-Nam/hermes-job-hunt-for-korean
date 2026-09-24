#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""원티드(wanted.co.kr) 채용공고 검색 — 공개 API, 인증 불필요.

Usage:
    python3 wanted_search.py "백엔드" [--limit 20] [--json]
    python3 wanted_search.py "데이터 엔지니어" --limit 10

출력: 공고 목록 (제목 · 회사 · 연봉힌트 · 링크). --json 시 원자료.
링크는 https://www.wanted.co.kr/wd/<id>.
"""
import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

SEARCH = "https://www.wanted.co.kr/api/chaos/search/v1/results"
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
UA = "Mozilla/5.0 (compatible; HermesJobBot/1.0)"
KST = timezone(timedelta(hours=9))
# NOTE (job-hunter-kit 벤더링): 레벨 필터는 hunt.py가 --profile 기준으로 일괄 적용.
# 이 스크립트는 필터 없이 원자료만 반환한다 (단일 책임).


def fetch(query: str, limit: int) -> list[dict]:
    qs = urllib.parse.urlencode({"query": query, "limit": limit})
    req = urllib.request.Request(f"{SEARCH}?{qs}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read(MAX_BYTES))   # 상한까지만(선언만 하고 안 쓰던 버그)
    positions = data.get("positions")
    # positions 는 list 이거나 {"data":[...]} 형태일 수 있어 방어적으로 처리
    if isinstance(positions, dict):
        positions = positions.get("data") or positions.get("list") or []
    return positions or []


def norm(p: dict) -> dict:
    pid = p.get("id")
    comp = p.get("company") or {}
    return {
        "id": pid,
        "title": p.get("position") or p.get("title") or p.get("name") or "",
        "company": comp.get("name") if isinstance(comp, dict) else (p.get("company_name") or ""),
        "reward": p.get("reward_total") or (p.get("reward") or {}).get("total") if isinstance(p.get("reward"), dict) else p.get("reward"),
        "location": p.get("address", {}).get("location") if isinstance(p.get("address"), dict) else p.get("location"),
        "url": f"https://www.wanted.co.kr/wd/{pid}" if pid else "",
    }


def to_rows(jobs: list[dict]) -> list[str]:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [f"| {j['company']} | {j['title']} | wanted |  | 발견 | {today} | {j['url']} |" for j in jobs]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all-levels", action="store_true",
                    help="(하위호환용. 무시됨 — 필터는 hunt.py가 적용)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()
    try:
        raw = fetch(a.query, a.limit)
    except Exception as e:
        print(f"에러: 원티드 조회 실패 — {e}", file=sys.stderr)
        return 1
    jobs = [norm(p) for p in raw if isinstance(p, dict)][:a.limit]
    if a.json:
        # 계약 스키마: [{"title","company","url",...}] — job-collect가 이걸 파싱한다(마크다운 재파싱 금지)
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
        return 0
    if a.table:
        print("| 회사 | 포지션 | 보드 | 적합도 | 상태 | 갱신일 | 공고링크 |")
        print("|---|---|---|---|---|---|---|")
        print("\n".join(to_rows(jobs)))
        return 0
    if not jobs:
        print(f'"{a.query}" 검색 결과 없음 (응답 구조가 바뀌었으면 --json 도 빈 리스트다).')
        return 0
    print(f'# 원티드 "{a.query}" 검색 — {len(jobs)}건\n')
    for j in jobs:
        line = f"- **{j['title']}** · {j['company']}"
        if j.get("location"):
            line += f" · {j['location']}"
        if j.get("reward"):
            line += f" · 보상 {j['reward']}"
        line += f"\n  {j['url']}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
