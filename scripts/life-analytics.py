#!/usr/bin/env python3
"""life-analytics — 위키 profile/mood.md·health.md의 날짜 섹션에서 라이프 지표를 파싱해
피어슨 상관·요일 패턴·최근 요약을 계산한다. (LLM 불필요, no_agent 크론용)

원본 수학: Lethe044/hermes-life-os demo/analytics.py (MIT) — 데이터 소스만 위키로 이식.
라인 문법: `### YYYY-MM-DD` 섹션 아래
  - 기분: 7/10 — 메모      - 에너지: 상|중|하
  - 수면: 6h | 6시간        - 스트레스: 4/10       - 집중: 5/10
"""
import json, os, re, sys
from collections import defaultdict
from datetime import date, datetime, timedelta

ROOT = os.environ.get("HERMES_DATA", "/opt/data")
FILES = ["wiki/profile/mood.md", "wiki/profile/health.md"]
_ENERGY = {"상": 3.0, "high": 3.0, "높": 3.0, "중": 2.0, "medium": 2.0, "하": 1.0, "low": 1.0, "낮": 1.0}
_KOR = {"mood": "기분", "energy": "에너지", "sleep": "수면", "stress": "스트레스", "focus": "집중"}
_DATE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})")


def _metric(line):
    m = re.match(r"^-\s*([^:：]+)[:：]\s*(.+)$", line.strip())
    if not m:
        return None
    key, val = m.group(1).strip(), m.group(2)
    if key.startswith("기분"):
        s = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*/\s*10", val)
        return ("mood", float(s.group(1))) if s else None
    if key.startswith("에너지"):
        for k, v in _ENERGY.items():
            if k in val:
                return ("energy", v)
        return None
    if key.startswith("수면"):
        s = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*(?:h|시간)", val)
        return ("sleep", float(s.group(1))) if s else None
    if key.startswith("스트레스"):
        s = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*/\s*10", val)
        return ("stress", float(s.group(1))) if s else None
    if key.startswith("집중"):
        s = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*/\s*10", val)
        return ("focus", float(s.group(1))) if s else None
    return None


_DONE = ("✅", "완료", "함", "done", "o", "O")

def parse_habits_goals(root=ROOT):
    """습관·목표 파싱(기존 metric 파이프라인과 분리).
    `- 습관: <이름> ✅` → 그날 그 습관 수행. `- 목표: <이름> | N%` → 진행률.
    반환: (habit_days {이름: [날짜...]}, goals {이름: [(날짜, pct)...]})"""
    habit_days, goals = defaultdict(list), defaultdict(list)
    for rel in FILES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        cur = None
        for line in open(p, encoding="utf-8"):
            d = _DATE.match(line)
            if d:
                cur = d.group(1); continue
            if not cur:
                continue
            m = re.match(r"^-\s*습관\s*[:：]\s*(.+)$", line.strip())
            if m and any(k in m.group(1) for k in _DONE):
                name = re.split(r"[✅|]|완료|done", m.group(1))[0].strip()
                if name and cur not in habit_days[name]:
                    habit_days[name].append(cur)
                continue
            g = re.match(r"^-\s*목표\s*[:：]\s*(.+?)\s*[|｜]\s*(\d+)\s*%", line.strip())
            if g:
                goals[g.group(1).strip()].append((cur, int(g.group(2))))
    return habit_days, goals


def habit_streaks(habit_days):
    """오늘(또는 최근일) 기준 연속 수행 스트릭. {이름: (스트릭, 마지막날)}"""
    out = {}
    for name, days in habit_days.items():
        ds = sorted(set(days))
        streak, last = 1, ds[-1]
        cur = datetime.strptime(last, "%Y-%m-%d").date()
        i = len(ds) - 2
        while i >= 0:
            prev = datetime.strptime(ds[i], "%Y-%m-%d").date()
            if (cur - prev).days == 1:
                streak += 1; cur = prev; i -= 1
            else:
                break
        out[name] = (streak, last)
    return out


def parse_wiki(root=ROOT):
    """{date: {metric: [values]}}"""
    series = defaultdict(lambda: defaultdict(list))
    for rel in FILES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        cur = None
        for line in open(p, encoding="utf-8"):
            d = _DATE.match(line)
            if d:
                cur = d.group(1)
                continue
            if cur:
                got = _metric(line)
                if got:
                    series[cur][got[0]].append(got[1])
    return {d: {m: sum(v) / len(v) for m, v in ms.items()} for d, ms in series.items()}


def pearson(x, y):
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    if vx == 0 or vy == 0:
        return None
    return max(-1.0, min(1.0, cov / (vx ** 0.5 * vy ** 0.5)))


def correlations(daily, min_days=4, min_abs_r=0.4):
    metrics = sorted({m for v in daily.values() for m in v})
    out = []
    for i in range(len(metrics)):
        for j in range(i + 1, len(metrics)):
            a, b = metrics[i], metrics[j]
            dates = [d for d, v in daily.items() if a in v and b in v]
            if len(dates) < min_days:
                continue
            r = pearson([daily[d][a] for d in dates], [daily[d][b] for d in dates])
            if r is None or abs(r) < min_abs_r:
                continue
            out.append({"a": a, "b": b, "r": round(r, 3), "n": len(dates),
                        "dir": "같이 움직임" if r > 0 else "반대로 움직임",
                        "강도": "강함" if abs(r) >= 0.7 else "중간"})
    return sorted(out, key=lambda c: abs(c["r"]), reverse=True)


def report(days=30):
    daily = parse_wiki()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    daily = {d: v for d, v in daily.items() if d >= cutoff}
    if not daily:
        return f"라이프 데이터 없음 (최근 {days}일). profile/mood.md·health.md에 `- 기분: 7/10` 형식으로 쌓이면 분석 시작."
    L = [f"📊 라이프 패턴 (최근 {days}일, 데이터 {len(daily)}일치)"]
    # 최근 7일 평균
    wk = {d: v for d, v in daily.items() if d >= (date.today() - timedelta(days=7)).isoformat()}
    if wk:
        avgs = defaultdict(list)
        for v in wk.values():
            for m, x in v.items():
                avgs[m].append(x)
        L.append("· 최근 7일: " + " / ".join(f"{_KOR[m]} {sum(x)/len(x):.1f}" for m, x in sorted(avgs.items())))
    # 기분 딥 (최근 연속 3일 <6)
    recent = sorted(daily)[-3:]
    moods = [daily[d].get("mood") for d in recent]
    if len(recent) == 3 and all(m is not None and m < 6 for m in moods):
        L.append(f"· ⚠ 기분 3일 연속 6 미만 ({recent[0]}~{recent[-1]})")
    # 요일 패턴 (mood, 요일별 2회+)
    wd = defaultdict(list)
    for d, v in daily.items():
        if "mood" in v:
            wd[datetime.strptime(d, "%Y-%m-%d").weekday()].append(v["mood"])
    pats = {k: sum(v) / len(v) for k, v in wd.items() if len(v) >= 2}
    if len(pats) >= 2:
        names = "월화수목금토일"
        hi, lo = max(pats, key=pats.get), min(pats, key=pats.get)
        if pats[hi] - pats[lo] >= 1.0:
            L.append(f"· 요일 패턴: {names[hi]}요일 높음({pats[hi]:.1f}) / {names[lo]}요일 낮음({pats[lo]:.1f})")
    # 상관
    for c in correlations(daily)[:3]:
        L.append(f"· {_KOR[c['a']]}↔{_KOR[c['b']]} {c['dir']} (r={c['r']}, {c['n']}일, {c['강도']})")
    # 습관 스트릭 (7일+ 축하 / 최근 끊김 인정)
    hd, goals = parse_habits_goals()
    today = date.today()
    for name, (streak, last) in sorted(habit_streaks(hd).items(), key=lambda x: -x[1][0]):
        last_d = datetime.strptime(last, "%Y-%m-%d").date()
        gap = (today - last_d).days
        if gap <= 1 and streak >= 7:
            L.append(f"· 🔥 습관 '{name}' {streak}일 연속 — 축하!")
        elif gap <= 1 and streak >= 3:
            L.append(f"· 습관 '{name}' {streak}일째")
        elif gap >= 2 and streak >= 3:
            L.append(f"· 습관 '{name}' 끊김({streak}일 하다 {gap}일 쉼) — 수치심 없이, 다시 시작하면 됨")
    # 목표 정체 (마지막 진행 7일+ 전 & 미완)
    for name, hist in goals.items():
        d0, pct = sorted(hist)[-1]
        stall = (today - datetime.strptime(d0, "%Y-%m-%d").date()).days
        if pct < 100 and stall >= 7:
            L.append(f"· 목표 '{name}' {pct}%에서 {stall}일째 정체 — 가벼운 넛지")
    if len(L) == 1:
        L.append("· 아직 패턴 없음 — 지표 2종+가 4일 이상 겹치면 상관 분석 시작.")
    return "\n".join(L)


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(f"{t}/wiki/profile")
        open(f"{t}/wiki/profile/mood.md", "w").write(
            "# mood\n### 2026-07-20\n- 기분: ~8/10 — 좋음(추정)\n### 2026-07-21\n- 기분: 4/10\n"
            "### 2026-07-22\n- 기분: 7/10\n### 2026-07-23\n- 기분: 3/10\n")
        open(f"{t}/wiki/profile/health.md", "w").write(
            "# health\n### 2026-07-20\n- 수면: 8h\n- 에너지: 상\n### 2026-07-21\n- 수면: 4시간\n"
            "### 2026-07-22\n- 수면: 7h\n### 2026-07-23\n- 수면: 3.5h\n")
        daily = parse_wiki(t)
        assert daily["2026-07-20"] == {"mood": 8.0, "sleep": 8.0, "energy": 3.0}, daily
        cs = correlations(daily)
        assert cs and cs[0]["a"] == "mood" and cs[0]["b"] == "sleep" and cs[0]["r"] > 0.9, cs
        # 습관·목표
        open(f"{t}/wiki/profile/health.md", "a").write(
            "### 2026-07-24\n- 습관: 운동 ✅\n### 2026-07-25\n- 습관: 운동 ✅\n- 목표: 면접준비 | 40%\n"
            "### 2026-07-26\n- 습관: 운동 ✅\n")
        hd, goals = parse_habits_goals(t)
        assert hd["운동"] == ["2026-07-24", "2026-07-25", "2026-07-26"], hd
        st = habit_streaks(hd)
        assert st["운동"][0] == 3 and st["운동"][1] == "2026-07-26", st
        assert goals["면접준비"] == [("2026-07-25", 40)], goals
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--json" in sys.argv:
        print(json.dumps(parse_wiki(), ensure_ascii=False, indent=1))
    else:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
        print(report(days))
