#!/usr/bin/env python3
"""daily-wiki-log.py — content-aware wiki change detector.
SHA256 해시로 실제 내용 변경만 감지한다(정본 — 이 스크립트 하나가 유일한 위키 변경 추적기).
"""
import hashlib, json, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from noteio import DRY_RUN, why_dry, write_atomic   # 쓰기 스위치·원자성 = 공용 계층

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
WIKI_DIR = Path(f"{DATA}/wiki")
STATE_FILE = Path(f"{DATA}/.wiki-hashes.json")
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y-%m-%d")

# 월 로테이션 — log.md 무한성장 방지: 첫 엔트리가 지난달이면 파일 통째로 _archive/log/<그 달>.md 이관.
# 변경 0건 조기종료보다 먼저 실행돼야 조용한 날에도 로테이션이 돈다.
# ponytail: 두 달 걸친 내용은 첫 엔트리 달로 묶임(매일 실행이라 오차 하루치) — 문제 되면 엔트리 단위 분할로
_lf = WIKI_DIR / "log.md"
if not DRY_RUN and _lf.exists():
    import re as _re
    _txt = _lf.read_text(encoding="utf-8")
    _m = _re.search(r"^## \[(\d{4}-\d{2})", _txt, _re.M)
    _cur = datetime.now(KST).strftime("%Y-%m")
    if _m and _m.group(1) != _cur:
        _dst = WIKI_DIR / "_archive" / "log" / (_m.group(1) + ".md")
        _dst.parent.mkdir(parents=True, exist_ok=True)
        if _dst.exists():
            _txt = _dst.read_text(encoding="utf-8").rstrip() + "\n\n" + _txt
        write_atomic(str(_dst), _txt)
        write_atomic(str(_lf), "# 위키 변경 로그 (" + _cur + ") — 이전 달: [[_archive/log/" + _m.group(1) + "|" + _m.group(1) + "]]\n\n")
        print("log.md 로테이션: " + _m.group(1) + " → _archive/log/", file=sys.stderr)

# Load previous hashes
prev = {}
if STATE_FILE.exists():
    try:
        prev = json.loads(STATE_FILE.read_text())
    except ValueError as e:
        # 빈 값으로 계속하면 위키 전체가 [NEW]로 재보고되고 원본이 덮인다 → 보존하고 중단
        if DRY_RUN:
            raise SystemExit(f"❌ .wiki-hashes.json 손상: {e}\n   (읽기 전용이라 파일은 그대로 뒀다)")
        bak = f"{STATE_FILE}.corrupt-{int(datetime.now(KST).timestamp())}"
        STATE_FILE.rename(bak)
        raise SystemExit(f"❌ .wiki-hashes.json 손상: {e}\n   원본 보존: {bak} (중단)")

# Scan all .md files
current = {}
changed = []
for f in sorted(WIKI_DIR.rglob("*.md")):
    # Skip hidden dirs, templates, _archive, .stversions
    parts = f.parts[len(WIKI_DIR.parts):]
    if any(p.startswith(".") for p in parts): continue
    if any(p.startswith("_") for p in parts if p != "_archive"): continue
    if "templates" in parts: continue
    
    rel = str(f.relative_to(WIKI_DIR))
    raw = f.read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    if rel == "log.md":
        continue  # 변경기록 장부 자신은 추적 제외(자기보고 루프 방지)

    # 파일명 자체가 정보다(회사·사람·건강·재정). 이 출력은 Discord로 배달되므로
    # private: true 노트는 이름을 내보내지 않고 건수만 센다.
    private = b"private: true" in raw[:400]
    current[rel] = {"h": h, "p": private}      # 삭제 시점엔 파일을 못 읽으니 private 여부를 상태에 남긴다
    prev_h = prev.get(rel, {}).get("h") if isinstance(prev.get(rel), dict) else prev.get(rel)
    if rel not in prev:
        changed.append(("NEW", rel, private))
    elif prev_h != h:
        changed.append(("MOD", rel, private))

# Detect deleted files
for rel in prev:
    if rel not in current:
        was_private = prev[rel].get("p", True) if isinstance(prev[rel], dict) else True
        # 옛 형식(해시 문자열만) 상태면 private 여부를 모른다 → 보수적으로 비공개 취급(이름 숨김)
        changed.append(("DEL", rel, was_private))

# Report
if not changed:
    sys.exit(0)  # silent — no changes

shown = [f"[{k}] {rel}" for k, rel, priv in changed if not priv]
hidden = sum(1 for _, _, priv in changed if priv)
entry = f"## [{TODAY}] daily-log | {len(changed)} files changed"
for c in shown:
    entry += f"\n- {c}"
if hidden:
    entry += f"\n- (비공개 노트 {hidden}건 — 이름 비공개)"

# 로그를 먼저 쓰고 나서 해시를 저장한다. 순서가 반대면 log.md 쓰기가 실패했을 때
# 해시는 이미 최신이라 다음 실행이 '변경 없음'으로 보고 그 기록이 영구히 사라진다.
if DRY_RUN:   # 보고는 하되 log.md·해시 상태는 건드리지 않는다(다음 실행이 같은 변경을 다시 본다)
    print(entry)
    print(f"[DRY-RUN] log.md·해시 상태 갱신 생략 — {why_dry()}", file=sys.stderr)
    sys.exit(0)

log_file = WIKI_DIR / "log.md"
with open(log_file, "a") as f:
    f.write("\n\n" + entry)
    f.flush()
    os.fsync(f.fileno())

write_atomic(str(STATE_FILE), json.dumps(current, indent=2))   # 로그 성공 뒤에만 상태 전진(원자적)

print(entry)  # stdout → delivered via cron (Discord)
