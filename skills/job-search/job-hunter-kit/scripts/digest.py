#!/usr/bin/env python3
"""job-hunter-kit 다이제스트: hunt.py --json → 신규만 Markdown → stdout.

no-agent 크론용. 빈 결과면 아무것도 출력하지 않는다 (조용한 크론).
Usage:
    python3 digest.py "백엔드" --state-dir ~/.hermes/job-hunter --limit 10
    python3 digest.py "백엔드" --state-dir ~/.hermes/job-hunter --limit 10 --all-levels
    python3 digest.py "백엔드" --no-state   # 상태 없이 매번 전체 (테스트용)
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HUNT = Path(__file__).resolve().parent / "hunt.py"


def main():
    ap = argparse.ArgumentParser(prog="digest.py")
    ap.add_argument("keyword")
    ap.add_argument("--state-dir", default="~/.hermes/job-hunter")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--boards", default="wanted,web")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--all-levels", action="store_true")
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--header", default="🔎 구직 다이제스트")
    a = ap.parse_args()

    cmd = [sys.executable, str(HUNT), a.keyword, "--limit", str(a.limit),
           "--boards", a.boards, "--timeout", str(a.timeout), "--json"]
    if a.profile:
        cmd += ["--profile", a.profile]
    if a.all_levels:
        cmd += ["--all-levels"]
    if not a.no_state:
        cmd += ["--state-dir", str(Path(a.state_dir).expanduser()), "--new-only"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("구직 다이제스트 실패: 검색 타임아웃", file=sys.stderr)
        return 2
    if out.returncode == 2:
        print("구직 다이제스트 실패: 보드 전부 실패\n%s" % out.stderr.strip()[:300],
              file=sys.stderr)
        return 2
    try:
        jobs = json.loads(out.stdout) if out.stdout.strip() else []
    except Exception:
        print("구직 다이제스트 실패: 결과 파싱 오류", file=sys.stderr)
        return 2
    if not jobs:
        return 0  # 신규 없음 → 조용히 종료 (no-agent 크론은 빈 stdout = 미발송)
    lines = ["%s — \"%s\" 신규 %d건" % (a.header, a.keyword, len(jobs)), ""]
    for j in jobs:
        lines.append("• **%s** · %s [%s]" % (j.get("title", ""), j.get("company", ""),
                                            j.get("board", "")))
        if j.get("url"):
            lines.append("  %s" % j["url"])
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
