#!/usr/bin/env python3
"""구직 스크래퍼·수집기 공유 필터 — search-profile.yaml을 단일 SoT로 읽는다.

각 스크래퍼가 _SENIOR 정규식을 따로 하드코딩하던 걸 여기로 통일.
search-profile.yaml 없거나 깨지면 아래 기본값(폴백)으로 동작(스탠드얼론 안전).
"""
import os
import re

_DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
_PROFILE = f"{_DATA}/search-profile.yaml"

_DEF_SENIOR = (r"senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b|chief|高[级級]|"
               r"리더|리더급|strategic|시니어|수석|책임|총괄|팀장|실장|본부장|매니저급|팀리드|파트장|그룹장|"
               r"\bintern(ship)?\b|인턴|체험형|\b\d{2,}년")
_DEF_EXCLUDE = (r"프로그래머|개발자|백엔드|프론트엔드|풀스택|앱\s*개발|웹\s*개발|서버\s*(운영|관리)|퍼블리셔|"
                r"devops|sre|\bqa\b|테스터|디자이너|일러스트|영상편집|번역|통역|간호|약사|의사|회계사|세무사|변호사|"
                r"생산직|물류\s*(관리|사원)|backend|frontend|full.?stack|android|ios\b")


_warned = []
_cache = {}


def _profile():
    """search-profile.yaml 로드. **깨져 있으면 중단한다(fail-closed)** —
    구직 필터는 '누구를 위한 수집인가'를 정하는 설정이라, 못 읽었을 때 제작자 기본값으로
    계속 도는 건 조용히 남의 기준으로 거르는 것과 같다. 파일이 아예 없는 경우만
    내장 기본값으로 동작하되(첫 설치·독립 실행), 경고를 크게 남긴다."""
    if "p" in _cache:
        return _cache["p"]
    import yaml
    try:
        with open(_PROFILE, encoding="utf-8") as fh:
            _cache["p"] = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        if not _warned:
            import sys
            print(f"⚠️ {_PROFILE} 없음 — 내장 기본 필터로 동작(제작자 기준!). 본인 프로필로 바꾸려면 이 파일을 두라.",
                  file=sys.stderr)
            _warned.append(1)
        _cache["p"] = {}
    except yaml.YAMLError as e:
        raise SystemExit(f"❌ search-profile.yaml 파싱 실패 — 중단한다(잘못된 기준으로 거르는 것보다 안전):\n   {e}\n"
                         f"   파일: {_PROFILE}")
    return _cache["p"]


def _f(key, default):
    v = (_profile().get("filters") or {}).get(key)
    return default if v is None else v  # 빈 값 = 의도적 해제(기본값 폴백 아님)


_sen = _f("senior_signals", _DEF_SENIOR)
SENIOR = re.compile("(?i)(" + _sen + ")") if _sen else re.compile(r"(?!x)x")  # 빈값=매치 없음
def _flatten_excl(v):
    if isinstance(v, dict):
        out = []
        for vals in v.values():
            out += vals if isinstance(vals, list) else [vals]
        return "|".join(out)
    if isinstance(v, list):
        return "|".join(v)
    return v or ""


EXCLUDE = re.compile("(?i)(" + (_flatten_excl(_f("exclude_roles", _DEF_EXCLUDE)) or _DEF_EXCLUDE) + ")")


def is_senior(title):
    return bool(SENIOR.search(title or ""))


def is_excluded(title):
    return bool(EXCLUDE.search(title or ""))


_DEF_TRACK = (r"영업|세일즈|sales|비즈니스|business|\bBDR?\b|\bSDR\b|\bAE\b|account|사업\s*개발|파트너십|partnership|"
              r"customer\s*success|오퍼레이션|operation|revops|\bGTM\b|enablement|데이터\s*분석|data\s*analyst|"
              r"business\s*analyst|\bPM\b|\bPO\b|product|프로덕트|전략\s*기획|경영\s*기획|\bAX\b|"
              r"AI\s*(자동화|automation|agent|에이전트)|\bFDE\b|solutions?\s*(engineer|consultant)|프리세일즈|pre-?sales|inside\s*sales|고객\s*성공|그로스|growth")
TRACK = re.compile("(?i)(" + _f("title_must_match", _DEF_TRACK) + ")")


def matches_track(title):
    """제목이 타깃 직군과 관련 있나 — 무관하면 수집 자체를 막는 게이트."""
    return bool(TRACK.search(title or ""))

# ── 크로스플랫폼 중복 판정용 정규화 키(퍼지 아님, 규칙기반) ──────────────
# 대괄호[회사]/꺾쇠는 노이즈=항상 제거. 소괄호는 (주)/(신입/경력) 등 수식어·법인격만
# 제거하고 (Customer Success) 같은 의미있는 건 유지 → 다른 직무를 같다고 오판 방지.
_QUAL = re.compile(r"(?i)신입|경력|정규직|계약직|인턴|채용|모집|공고|담당자|담당|영입|급구|우대|\d+\s*년|\d+\s*개월|재택|본사|d-?\d")
_LEGAL = re.compile(r"(?i)주식회사|㈜|유한회사|inc|corp|ltd|company|코리아|korea")

def _strip(s):
    s = re.sub(r"\[.*?\]|<.*?>", "", s or "")  # 대괄호/꺾쇠 항상 제거
    # 소괄호: 내용이 ≤2자(주/유/CS)거나 수식어면 제거, 아니면 유지
    s = re.sub(r"\(([^)]*)\)", lambda m: "" if (len(m.group(1).strip()) <= 2 or _QUAL.search(m.group(1))) else m.group(0), s)
    return s

_ALIAS = None
def _load_alias():
    """search-profile.yaml의 company_aliases(별칭→표준명 매핑)를 1회 로드·캐시.
    규칙으로 못 잡는 진짜 별칭(당근=당근마켓, 쿠팡=Coupang, 토스=비바리퍼블리카)을
    명시적으로 합치는 안전한 방법. 유사도 매칭과 달리 오검출(다른 공고 병합)이 없다."""
    global _ALIAS
    if _ALIAS is None:
        _ALIAS = {}
        try:
            import yaml
            p = yaml.safe_load(open(_PROFILE, encoding="utf-8")) or {}
            for canon, variants in (p.get("company_aliases") or {}).items():
                cn = re.sub(r"[^0-9a-z가-힣]", "", str(canon).lower())
                for v in (variants if isinstance(variants, list) else [variants]):
                    _ALIAS[re.sub(r"[^0-9a-z가-힣]", "", str(v).lower())] = cn
        except Exception:
            pass
    return _ALIAS

def norm_company(c):
    # 회사명의 소괄호는 영문 병기(네이버(NAVER))·부가설명일 뿐 dedup 노이즈 — 전부 제거.
    # (norm_position과 대칭. 의미있는 소괄호가 회사명에 오는 경우는 거의 없다)
    c = re.sub(r"\([^)]*\)", "", _strip(c))
    k = re.sub(r"[^0-9a-z가-힣]", "", _LEGAL.sub("", c).lower())
    return _load_alias().get(k, k)   # 별칭이면 표준명으로 접기

def norm_position(p):
    # 괄호 내용 전부 제거: (AI 기반 전사업무 자동화) 같은 부가설명은 dedup 노이즈
    p = re.sub(r"\(.*?\)", "", _strip(p))
    return re.sub(r"[^0-9a-z가-힣]", "", _QUAL.sub("", p).lower())

def norm_key(company, position):
    """정규화 키 — 같으면 크로스플랫폼 동일공고로 판단."""
    return norm_company(company) + "|" + norm_position(position)

# ── 제목 연차 게이트 (수집기 _EXP와 동일 기준, 커밋/--stage 경로 대칭용) ──
_flt = _f("__dummy__", {}) if False else None
def _prof_int(key, default):
    try:
        import yaml
        p = yaml.safe_load(open(_PROFILE, encoding="utf-8")) or {}
        return int((p.get("filters") or {}).get(key) or default)
    except Exception:
        return default
def _prof_str(key, default=""):
    try:
        import yaml
        p = yaml.safe_load(open(_PROFILE, encoding="utf-8")) or {}
        v = (p.get("filters") or {}).get(key)
        return default if v is None else v  # 빈 값 = 의도적 해제(기본값 폴백 아님)
    except Exception:
        return default
def _exp_re(n):
    n = max(1, min(9, int(n)))
    d = "(?:[%d-9]|\\d{2,})" % n
    return re.compile(d + r"\s*년\s*(?:이상|차|\+|플러스|~)|" + d + r"\s*\+?\s*years|minimum\s*" + d + "|" + d + r"\s*[~∼～-]\s*\d+\s*년")
_TITLE_YR = _prof_int("title_exp_min_years", 3)
_AX_YR = _prof_int("ax_exp_min_years", 6)
_AX_RE = re.compile("(?i)(" + _prof_str("ax_title", "$^") + ")") if _prof_str("ax_title") else None
_EXP_T = _exp_re(_TITLE_YR)
_EXP_AXT = _exp_re(_AX_YR)
def exceeds_exp(title):
    """제목에 임계 연차+ 요구가 있으면 True. AX 직무는 완화 임계(ax_exp_min_years) 적용."""
    title = title or ""
    ax = bool(_AX_RE and _AX_RE.search(title))
    return bool((_EXP_AXT if ax else _EXP_T).search(title))
