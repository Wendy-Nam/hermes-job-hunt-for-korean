#!/usr/bin/env python3
"""원티드 유사공고 1-hop 확장 — 공고의 직군 태그·스킬 태그로 재검색해 '비슷한 공고'를 긁는다.

플랫폼 추천엔진의 협업필터링을 흉내내는 저비용 방법: ✅/🔧 판정 받은 공고를 시드로 넣으면
같은 직군·스킬 계열의 공고가 키워드 검색에 안 걸리던 것까지 딸려 나온다.

Usage:
    python3 similar_jobs.py <원티드 URL 또는 id> [--limit 15] [--json] [--table]
    python3 similar_jobs.py https://www.wanted.co.kr/wd/372965 --limit 15
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

DETAIL = "https://www.wanted.co.kr/api/v4/jobs/{id}"
BY_TAG = "https://www.wanted.co.kr/api/v4/jobs?tag_type_ids={tag}&country=kr&job_sort=job.latest_order&locations=all&limit={limit}"
SEARCH = "https://www.wanted.co.kr/api/chaos/search/v1/results"
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
UA = "Mozilla/5.0 (compatible; HermesJobBot/1.0)"
KST = timezone(timedelta(hours=9))
_SENIOR = re.compile(r"(?i)(senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b|chief|高[级級]|리더|리더급|strategic|시니어|수석|책임|총괄|팀장|실장|본부장|팀리드|파트장|그룹장|\bintern(ship)?\b|인턴|체험형)")


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read(MAX_BYTES))   # 상한까지만(선언만 하고 안 쓰던 버그)


def parse_id(ref: str) -> str:
    m = re.search(r"/wd/(\d+)", ref)
    if m:
        return m.group(1)
    if ref.isdigit():
        return ref
    raise ValueError(f"원티드 URL/id 아님: {ref}")


def seed_tags(job_id: str) -> tuple[dict, list[int]]:
    """공고 상세에서 직군 카테고리 태그 ID를 뽑는다 (가장 강한 유사 신호)."""
    d = _get(DETAIL.format(id=job_id))
    job = d.get("job") or {}
    tags: list[int] = []
    for t in job.get("category_tags") or []:
        tid = t.get("id")
        if isinstance(tid, int) and tid not in tags:
            tags.append(tid)
    return job, tags[:4]


def by_tag(tag_id: int, limit: int) -> list[dict]:
    d = _get(BY_TAG.format(tag=tag_id, limit=limit))
    return d.get("data") or []


def search(query: str, limit: int) -> list[dict]:
    qs = urllib.parse.urlencode({"query": query, "limit": limit})
    data = _get(f"{SEARCH}?{qs}")
    positions = data.get("positions")
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
        "url": f"https://www.wanted.co.kr/wd/{pid}" if pid else "",
    }


def to_rows(jobs: list[dict]) -> list[str]:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [f"| {j['company']} | {j['title']} | wanted(유사) |  | 발견 | {today} | {j['url']} |" for j in jobs]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="원티드 공고 URL 또는 id")
    ap.add_argument("--limit", type=int, default=15, help="최종 유사공고 수")
    ap.add_argument("--all-levels", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()

    try:
        src_id = parse_id(a.ref)
        src, tags = seed_tags(src_id)
    except Exception as e:
        print(f"에러: 시드 공고 조회 실패 — {e}", file=sys.stderr)
        return 1

    seen: dict = {}
    for tid in tags:
        try:
            for p in by_tag(tid, max(15, a.limit)):
                j = norm(p)
                if not j["id"] or str(j["id"]) == str(src_id) or j["id"] in seen:
                    continue
                if not a.all_levels and _SENIOR.search(j["title"] or ""):
                    continue
                seen[j["id"]] = j
        except Exception:
            continue
    # 태그가 없거나 결과가 빈약하면 포지션명 키워드 검색으로 폴백
    if len(seen) < 3:
        pos = src.get("position") if isinstance(src.get("position"), str) else ""
        if pos:
            try:
                for p in search(pos, max(15, a.limit)):
                    j = norm(p)
                    if not j["id"] or str(j["id"]) == str(src_id) or j["id"] in seen:
                        continue
                    if not a.all_levels and _SENIOR.search(j["title"] or ""):
                        continue
                    seen[j["id"]] = j
            except Exception:
                pass
    jobs = list(seen.values())[: a.limit]
    tag_desc = ", ".join(str(t) for t in tags) or "없음(키워드 폴백)"

    if a.json:
        print(json.dumps({"seed": {"id": src_id, "title": src.get("position") if isinstance(src.get("position"), str) else "", "tag_ids": tags}, "similar": jobs}, ensure_ascii=False, indent=2))
        return 0
    if a.table:
        print("| 회사 | 포지션 | 보드 | 적합도 | 상태 | 갱신일 | 공고링크 |")
        print("|---|---|---|---|---|---|---|")
        print("\n".join(to_rows(jobs)))
        return 0
    print(f"# 유사공고 1-hop (시드 wd/{src_id}, 태그ID: {tag_desc}) — {len(jobs)}건\n")
    for j in jobs:
        print(f"- **{j['title']}** · {j['company']}\n  {j['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
