#!/usr/bin/env python3
"""profile-flow — mood/health 상단 '최근 흐름' 한 줄을 life-analytics로 결정론 갱신.
마커 `<!-- flow -->` 라인만 재작성(본문·시그널 섹션 불가침). 스윕이 호출."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.environ.get("HERMES_DATA", "/opt/data"), "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "la", os.path.join(os.environ.get("HERMES_DATA", "/opt/data"), "scripts", "life-analytics.py"))
la = importlib.util.module_from_spec(spec); spec.loader.exec_module(la)

DATA = os.environ.get("HERMES_DATA", "/opt/data")
from datetime import date, timedelta, datetime
from collections import defaultdict

MARK = re.compile(r"^> 📈 최근 흐름:.*$", re.M)


def _flow_lines():
    """mood/health 각각의 한 줄 요약을 만든다."""
    daily = la.parse_wiki()
    wk = {d: v for d, v in daily.items() if d >= (date.today() - timedelta(days=7)).isoformat()}
    avg = defaultdict(list)
    for v in wk.values():
        for m, x in v.items():
            avg[m].append(x)
    a = {m: sum(x) / len(x) for m, x in avg.items()}
    # mood 줄
    mood_bits = []
    if "mood" in a: mood_bits.append(f"기분 {a['mood']:.1f}")
    if "stress" in a: mood_bits.append(f"스트레스 {a['stress']:.1f}")
    if "energy" in a: mood_bits.append(f"에너지 {a['energy']:.1f}")
    if "focus" in a: mood_bits.append(f"집중 {a['focus']:.1f}")
    # health 줄
    health_bits = []
    if "sleep" in a: health_bits.append(f"수면 {a['sleep']:.1f}h")
    hd, _ = la.parse_habits_goals()
    for name, (streak, last) in la.habit_streaks(hd).items():
        if (date.today() - datetime.strptime(last, "%Y-%m-%d").date()).days <= 1 and streak >= 3:
            health_bits.append(f"{name} {streak}일째")
    n = len(wk)
    mood = f"> 📈 최근 흐름: 최근 7일 " + (" · ".join(mood_bits) if mood_bits else "데이터 부족") + f" ({n}일치)"
    health = f"> 📈 최근 흐름: 최근 7일 " + (" · ".join(health_bits) if health_bits else "데이터 부족") + f" ({n}일치)"
    return {"mood.md": mood, "health.md": health}


def main():
    flows = _flow_lines()
    for f, line in flows.items():
        p = f"{DATA}/wiki/profile/{f}"
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        if MARK.search(t):
            t = MARK.sub(line, t, count=1)
        else:  # 제목(# ...) 다음 줄에 삽입
            t = re.sub(r"(^# .*$)", r"\1\n\n" + line.replace("\\", "\\\\"), t, count=1, flags=re.M)
        # 원자적 저장
        tmp = p + f".tmp{os.getpid()}"
        open(tmp, "w", encoding="utf-8").write(t)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    print("profile-flow:", " | ".join(flows.values()))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import tempfile
        d = tempfile.mkdtemp(); os.makedirs(f"{d}/wiki/profile"); os.makedirs(f"{d}/scripts")
        import shutil
        shutil.copy(os.path.join(DATA, "scripts", "life-analytics.py"), f"{d}/scripts/life-analytics.py")
        open(f"{d}/wiki/profile/mood.md", "w").write("---\ntitle: mood\n---\n# mood\n### 2026-07-26\n- 기분: 7/10\n")
        open(f"{d}/wiki/profile/health.md", "w").write("---\ntitle: health\n---\n# health\n### 2026-07-26\n- 수면: 6h\n")
        os.environ["HERMES_DATA"] = d
        spec2 = importlib.util.spec_from_file_location("la2", f"{d}/scripts/life-analytics.py")
        la = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(la)
        globals()["DATA"] = d
        main()
        assert "최근 흐름" in open(f"{d}/wiki/profile/mood.md").read()
        main()  # 멱등 — 두 번째도 마커 갱신
        assert open(f"{d}/wiki/profile/mood.md").read().count("최근 흐름") == 1, "마커 중복"
        print("selftest OK")
    else:
        main()
