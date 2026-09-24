#!/usr/bin/env python3
"""job_collect.profile — search-profile.yaml 로딩 및 연차 게이트 정규식 생성(job-collect.py 원본에서 이식, 동작 동일)."""
import re
import sys

from . import config

# ── 폴백 기본값: search-profile.yaml 없거나 깨지면 이걸로(=현재 동작 그대로) ──
_DEF_BLOCKS = [
    ["sales operations", "revenue operations", "business operations", "세일즈 오퍼레이션"],
    ["business development", "사업개발", "파트너십", "기술영업", "AI 자동화", "AX"],
    ["data analyst", "business analyst", "데이터 분석가", "product operations"],
    ["영업기획", "전략기획", "customer success", "inside sales", "AI automation", "AX"],
]
_DEF_SENIOR = r"senior|\bsr\.?\b|staff|principal|lead|director|head|리더|리더급|strategic|시니어|수석|책임|팀장|실장|본부장|매니저급|\b\d{2,}년"
_DEF_EXCLUDE = r"프로그래머|개발자|백엔드|프론트엔드|풀스택|앱\s*개발|웹\s*개발|서버\s*(운영|관리)|퍼블리셔|devops|sre|\bqa\b|테스터|디자이너|일러스트|영상편집|번역|통역|간호|약사|의사|회계사|세무사|변호사|생산직|물류\s*(관리|사원)|backend|frontend|full.?stack|android|ios\b"
_DEF_BOARDS = ["wanted", "saramin", "jobkorea", "linkedin", "ats"]


def _flatten_excl_jc(v):
    if isinstance(v, dict):
        out = []
        for vals in v.values():
            out += vals if isinstance(vals, list) else [vals]
        return "|".join(out)
    if isinstance(v, list):
        return "|".join(v)
    return v or ""


def load_profile():
    """search-profile.yaml에서 '나에 대한 것'을 읽는다. 없거나 깨지면 기본값 폴백."""
    p = {}
    try:
        import yaml
        with open(config.PROFILE_PATH, encoding="utf-8") as fh:
            p = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        print(f"⚠️ {config.PROFILE_PATH} 없음 — 내장 기본값으로 동작(제작자 기준!). 본인 프로필 yaml을 두라.", file=sys.stderr)
        p = {}
    except Exception as e:
        # 깨진 설정으로 계속 돌면 '남의 기준'으로 수집·거부하고 그 결과가 장부에 영구 기록된다 → 중단
        raise SystemExit(f"❌ search-profile.yaml 파싱 실패 — 수집 중단(잘못된 기준으로 거르는 것보다 안전): {e}")
    flt = (p.get("filters") or {})
    return {
        "limit": int(p.get("limit_per_board") or 20),
        "body_cap": int(p.get("body_fetch_cap") or 35),
        "blocks": p.get("keyword_blocks") or _DEF_BLOCKS,
        "senior": flt.get("senior_signals") if flt.get("senior_signals") is not None else _DEF_SENIOR,
        "exclude": _flatten_excl_jc(flt.get("exclude_roles")) or _DEF_EXCLUDE,
        "title_yr": int(flt.get("title_exp_min_years") or 3),
        "body_yr": int(flt.get("body_exp_min_years") or 5),
        "ax_title": flt.get("ax_title") or "",
        "ax_yr": int(flt.get("ax_exp_min_years") or 6),
        "exc_noax": flt.get("exclude_unless_ax") or "",
        "admin_b": flt.get("body_admin_signals") or "",
        "core_b": flt.get("body_core_signals") or "",
        "boards": p.get("boards") or _DEF_BOARDS,
        "track": flt.get("title_must_match") or "",
    }


def _exp_title(n):
    n = max(1, min(9, int(n)))
    d = "(?:[%d-9]|\\d{2,})" % n
    return re.compile(d + r"\s*년\s*(?:이상|차|\+|플러스|~)|" + d + r"\s*\+?\s*years|minimum\s*" + d
                      + "|" + d + r"\s*[~∼～-]\s*\d+\s*년")   # 범위 표기(예: 3~5년)도 시작 연차 기준 컷


def _exp_body(n):
    n = max(1, min(9, int(n)))
    d = "(?:[%d-9]|\\d{2,})" % n
    return re.compile(d + r"\s*년\s*(?:이상|차|\+)|" + d + r"\s*\+?\s*years|minimum\s*(?:of\s*)?" + d + r"\s*years"
                      + "|" + d + r"\s*[~∼～-]\s*\d+\s*년")
