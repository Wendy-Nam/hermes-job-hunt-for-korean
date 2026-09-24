#!/usr/bin/env python3
"""우선도 자동 파생(2026-07-10 v2): 우선도 = 티어점 + 트랙점 + 적합도점. 손채점 금지 — 이 스크립트가 유일 계산자.

  티어점: S=5 · A=4 · B=3 · C=2(기본) · D=0
  적합도점: ✅=0.9 · 🔧=0.5 · 미판정=0.2 · ❌=0
  트랙점: search-profile.yaml track_bonus 첫 매치(0~1.5)
  → 가중합, 스케일 0~7.4. 트랙점으로 인접 티어 역전 가능(의도된 설계 — 절대우선 원하면 track_bonus=0).

사용: priority-recalc.py [--file <노트경로>]   (인자 없으면 postings 전체)
job-collect가 런 끝에, job-stage-commit이 노트 생성 후에 호출한다(자가치유).
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noteio import note_lock, write_atomic   # 노트 쓰기는 반드시 이 계층으로(잠금+원자성)

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
POSTINGS = f"{DATA}/wiki/automation/job-hunting/postings"
TIER_PT = {"S": 5.0, "A": 4.0, "B": 3.0, "C": 2.0, "D": 0.0}
FIT_PT = {"✅": 0.9, "🔧": 0.5, "❌": 0.0}

_DEF_TRACK_BONUS = [
    (r"\bAX\b|AI\s*(자동화|automation|agent|에이전트|교육|도입|transformation)|\bRPA\b|업무\s*자동화", 1.5),
    (r"자동화|automation|revops|revenue\s*operations|sales\s*operations|business\s*operations|\bGTM\b|enablement", 1.0),
    (r"기술영업|솔루션|프리세일즈|pre-?sales|sales\s*engineer|solutions?\s*(engineer|consultant)|\bFDE\b", 0.7),
    (r"영업|세일즈|sales|\bBDR\b|\bSDR\b|\bAE\b|account|customer\s*success|고객\s*성공", 0.3),
]

def _track_bonus():
    """트랙 가산점 표. 설정이 깨졌으면 조용히 기본값으로 가지 않는다 —
    같은 yaml에 대해 수집기는 중단(fail-closed)인데 여기만 폴백하면 도구별 정책이 어긋나고,
    잘못된 점수가 노트에 영구 기록된다."""
    try:
        import yaml
        with open(f"{DATA}/search-profile.yaml", encoding="utf-8") as fh:
            p = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return []                      # 파일 없음 = 트랙 가산점 없음(첫 설치·독립 실행)
    except Exception as e:
        raise SystemExit(f"❌ search-profile.yaml 파싱 실패 — 우선도 계산 중단(잘못된 점수를 노트에 "
                         f"박는 것보다 안전): {e}")
    rows = p.get("track_bonus") or []
    try:
        return [(re.compile("(?i)(" + pat + ")"), pts) for pat, pts in rows]
    except (TypeError, ValueError, re.error) as e:
        raise SystemExit(f"❌ track_bonus 설정이 잘못됨 — 우선도 계산 중단: {e}")


TRACK_BONUS = _track_bonus()


def recalc(fp):
    t = open(fp, encoding="utf-8", errors="ignore").read()
    mt = re.search(r'^티어:\s*"?([SABCD])', t, re.M)
    tier_pt = TIER_PT.get(mt.group(1) if mt else "C", 2.0)
    mf = re.search(r'^적합도:\s*"?(.)', t, re.M)
    fit_pt = FIT_PT.get(mf.group(1) if mf else "", 0.2)
    mp = re.search(r'^포지션:\s*"?([^"\n]+)', t, re.M)
    pos = mp.group(1) if mp else ""
    tr_pt = next((pts for rx, pts in TRACK_BONUS if rx.search(pos)), 0.0)
    score = f"{tier_pt + tr_pt + fit_pt:.1f}"
    if re.search(r"^우선도:", t, re.M):
        new = re.sub(r'^우선도:.*$', f'우선도: "{score}"', t, count=1, flags=re.M)
    else:  # 티어 줄 뒤에 삽입, 없으면 적합도 뒤
        new = re.sub(r'(^티어:.*$)', r'\1' + f'\n우선도: "{score}"', t, count=1, flags=re.M)
        if new == t:
            new = re.sub(r'(^적합도:.*$)', r'\1' + f'\n우선도: "{score}"', t, count=1, flags=re.M)
        if new == t:
            new = re.sub(r'(^회사:.*$)', r'\1' + f'\n우선도: "{score}"', t, count=1, flags=re.M)
    if new != t:
        write_atomic(fp, new)
        return True
    return False


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--file":
        files = [sys.argv[2]]
    else:
        files = sorted(glob.glob(f"{POSTINGS}/*.md"))
    with note_lock():          # 노트 편집·판정 커밋과 겹쳐도 서로 덮지 않게(jsc가 부른 경우엔 이미 보유 → 통과)
        changed = sum(1 for f in files if os.path.isfile(f) and recalc(f))
    print(f"우선도 재계산: {changed}/{len(files)} 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
