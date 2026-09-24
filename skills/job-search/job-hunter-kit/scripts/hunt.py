#!/usr/bin/env python3
"""job-hunter-kit 통합 검색: wanted + web 합산, dedup, 레벨 필터, 상태 저장.

Usage:
    python3 hunt.py "백엔드" [--limit 10] [--json] [--all-levels]
                             [--boards wanted,web] [--profile profile.yaml]
                             [--state-dir ~/.hermes/job-hunter]
                             [--new-only] [--timeout 20]

- 보드 스크립트는 SKILL_DIR 내부에 벤더링됨 (scripts/boards/).
  외부 job-search/*. 스킬에 의존하지 않는다 → 남의 머신에서도 동작.
- dedup 키: wanted ID > URL 정규화 > 회사|포지션 정규화(크로스보드).
- 레벨 필터: --profile (search-profile.yaml 호환, filters.senior_signals).
  없으면 내장 기본값. 깨진 yaml은 fail-closed(중단).
- --state-dir: seen.json(본 URL 장부) 유지. --new-only면 신규만 출력.
- 출력: 기본 마크다운(텔레그램용), --json 시 [{"title","company","board","url"}].
- 종료코드: 0=정상, 1=사용법 오류, 2=보드 전부 실패.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BOARDS_DIR = SKILL_DIR / "scripts" / "boards"
DEFAULT_PROFILE = SKILL_DIR / "profile.example.yaml"

KST = timezone(timedelta(hours=9))

_DEF_SENIOR = (r"senior|\bsr\.?\b|staff|principal|\blead\b|director|head\s+of|\bvp\b"
               r"|chief|리더|시니어|수석|책임|총괄|팀장|실장|본부장|팀리드|"
               r"\bintern(ship)?\b|인턴|체험형")


def _read_simple_senior(path):
    """pyyaml 없이 senior_signals 한 줄만 뽑는 최소 파서. 없으면 None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return None, "unreadable"
    m = re.search(r"^[ \t]*senior_signals:[ \t]*('(.*)'|\"(.*)\"|(.*?))[ \t]*$",
                  text, re.M)
    if not m:
        return None, "no-key"
    v = m.group(2) if m.group(2) is not None else (
        m.group(3) if m.group(3) is not None else m.group(4))
    v = (v or "").strip()
    if not v or v in ("''", '""'):
        return "", "disabled(empty)"
    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
        v = v[1:-1]
    return v, "simple"


def load_senior(profile):
    """(compiled_re, source_label) — 프로필 > 내장기본값. 깨진 yaml은 중단."""
    if profile is None:
        profile = DEFAULT_PROFILE
    if Path(profile).exists():
        try:
            import yaml
            has_yaml = True
        except ImportError:
            has_yaml = False
        if not has_yaml:
            v, how = _read_simple_senior(profile)
            if how == "unreadable":
                return re.compile("(?i)(%s)" % _DEF_SENIOR), "builtin(unreadable)"
            if how == "no-key":
                print("경고: %s에 filters.senior_signals 없음 — 내장 기본값 사용." % profile,
                      file=sys.stderr)
                return re.compile("(?i)(%s)" % _DEF_SENIOR), "builtin(no-key)"
            if how == "disabled(empty)":
                return re.compile(r"(?!x)x"), "disabled(empty)"
            try:
                return re.compile("(?i)(%s)" % v), "%s(simple)" % profile
            except re.error as e:
                raise SystemExit("❌ senior_signals 정규식 오류 — 중단한다: %s" % e)
        try:
            data = yaml.safe_load(Path(profile).read_text(encoding="utf-8")) or {}
        except Exception as e:
            raise SystemExit("❌ 프로필 파싱 실패 — 중단한다:\n   %s\n   파일: %s" % (e, profile))
        v = (data.get("filters") or {}).get("senior_signals")
        if v is None:
            print("경고: %s에 filters.senior_signals 없음 — 내장 기본값 사용." % profile,
                  file=sys.stderr)
            return re.compile("(?i)(%s)" % _DEF_SENIOR), "builtin(no-key)"
        if not v:  # 빈 값 = 의도적 해제
            return re.compile(r"(?!x)x"), "disabled(empty)"
        try:
            return re.compile("(?i)(%s)" % v), str(profile)
        except re.error as e:
            raise SystemExit("❌ senior_signals 정규식 오류 — 중단한다: %s" % e)
    return re.compile("(?i)(%s)" % _DEF_SENIOR), "builtin(no-file)"


def run_board(path, kw, limit, timeout):
    try:
        out = subprocess.run(
            [sys.executable, str(path), kw, "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], "%s: 타임아웃(%ss)" % (path.stem, timeout)
    except Exception as e:
        return [], "%s: 실행 실패(%s)" % (path.stem, e)
    if out.returncode != 0:
        return [], "%s: 실패(rc=%s): %s" % (path.stem, out.returncode,
                                           out.stderr.strip()[:120])
    try:
        data = json.loads(out.stdout)
        if isinstance(data, list):
            return data, ""
        return [], "%s: JSON이 리스트 아님" % path.stem
    except Exception:
        return [], "%s: JSON 파싱 실패" % path.stem


def _norm_text(s):
    s = re.sub(r"\[.*?\]|<.*?>", "", s or "")
    s = re.sub(r"\(.*?\)", "", s)  # 소괄호 안은 법인격·수식어 → dedup 노이즈
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def dedup_key(job):
    url = job.get("url", "") or ""
    m = re.search(r"wanted\.co\.kr/wd/(\d+)", url)
    if m:
        return "wanted:" + m.group(1)
    if url:
        u = (url or "").strip().lower()
        u = re.sub(r"^https?://", "", u)
        u = re.sub(r"^www\.", "", u)
        u = u.split("?")[0].rstrip("/")
        if u:
            return "url:" + u
    return "kp:%s|%s" % (_norm_text(job.get("company", "")),
                         _norm_text(job.get("title", "")))


def load_seen(state_dir):
    try:
        return set(json.loads((state_dir / "seen.json").read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(state_dir, keys):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "seen.json").write_text(
        json.dumps(sorted(keys), ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(prog="hunt.py")
    ap.add_argument("keyword")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all-levels", action="store_true")
    ap.add_argument("--boards", default="wanted,web",
                    help="사용할 보드 (콤마 구분, 기본 wanted,web)")
    ap.add_argument("--profile", default=None, help="시니어 필터 프로필 yaml")
    ap.add_argument("--state-dir", default=None, help="seen.json 저장 디렉토리")
    ap.add_argument("--new-only", action="store_true", help="seen에 없는 신규만 출력")
    ap.add_argument("--timeout", type=int, default=20, help="보드당 타임아웃 초")
    a = ap.parse_args()

    senior_re, senior_src = load_senior(Path(a.profile) if a.profile else None)

    boards = [b.strip() for b in a.boards.split(",") if b.strip()]
    board_files = {"wanted": BOARDS_DIR / "wanted_search.py",
                   "web": BOARDS_DIR / "web_job_search.py"}
    for b in boards:
        if b not in board_files:
            print("Unknown board: %s. Available: wanted, web" % b, file=sys.stderr)
            return 1
        if not board_files[b].exists():
            print("보드 스크립트 없음: %s" % board_files[b], file=sys.stderr)
            return 1

    per = max(1, min(a.limit, 50))
    all_jobs, errors = [], []
    for b in boards:
        jobs, err = run_board(board_files[b], a.keyword, per, a.timeout)
        if err:
            print("경고: %s" % err, file=sys.stderr)
            errors.append(err)
            continue
        for j in jobs:
            j["board"] = b
        all_jobs.append((b, jobs))

    seen, jobs = set(), []
    for _, js in all_jobs:
        for j in js:
            k = dedup_key(j)
            if k in seen:
                continue
            seen.add(k)
            j["_key"] = k
            jobs.append(j)
    if not a.all_levels:
        jobs = [j for j in jobs if not senior_re.search(j.get("title", "") or "")]

    state_dir = Path(a.state_dir).expanduser() if a.state_dir else None
    if state_dir:
        prev = load_seen(state_dir)
        new_jobs = [j for j in jobs if j["_key"] not in prev]
        save_seen(state_dir, prev | {j["_key"] for j in jobs})
        if a.new_only:
            jobs = new_jobs

    counts = {b: len(js) for b, js in all_jobs}
    if a.json:
        out = [{k: j.get(k, "") for k in ("title", "company", "board", "url")}
               for j in jobs]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        today = datetime.now(KST).strftime("%Y-%m-%d")
        print('# 구직 헌트 "%s" — %d건 (%s, 필터:%s)' % (a.keyword, len(jobs), today, senior_src))
        print("# 보드: " + " ".join("%s=%d" % (b, counts.get(b, 0)) for b in boards)
              + (" | 신규만" if a.new_only and state_dir else ""))
        if errors:
            print("# 경고 %d건: %s" % (len(errors), " / ".join(errors)))
        print()
        for j in jobs:
            print("- **%s** · %s · [%s]" % (j.get("title", ""), j.get("company", ""),
                                           j.get("board", "")))
            if j.get("url"):
                print("  %s" % j["url"])
    if not all_jobs:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
