#!/usr/bin/env python3
"""위키 skills-reference.md의 '활성 스킬 카탈로그' 섹션을 실제 렌더 기준으로 재생성.

Hermes가 매 턴 시스템 프롬프트에 넣는 활성 스킬 목록(= disabled/platform 필터 반영된
ground truth)을 그대로 위키에 동기화한다. 컨테이너 안에서 실행해야 한다:

    docker exec -u 10000 <container> python3 /opt/data/bin/wiki-skills-sync.py
"""
import re
import sys
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noteio import note_lock, write_atomic   # 위키 파일 쓰기도 공용 계층(잠금·원자성·쓰기스위치)

sys.path.insert(0, "/opt/hermes")
from agent.prompt_builder import build_skills_system_prompt  # noqa: E402

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
WIKI = Path(f"{DATA}/wiki/hermes/skills-reference.md")
BEGIN = "<!-- SKILLS-CATALOG:BEGIN (auto-generated — edit via /opt/data/bin/wiki-skills-sync.py) -->"
END = "<!-- SKILLS-CATALOG:END -->"
KST = timezone(timedelta(hours=9))


def parse_index(rendered: str) -> dict[str, list[tuple[str, str]]]:
    """<available_skills> 블록을 {카테고리: [(스킬, 설명), ...]} 로 파싱."""
    m = re.search(r"<available_skills>(.*?)</available_skills>", rendered, re.S)
    if not m:
        raise SystemExit("available_skills 블록을 못 찾음 — prompt_builder 출력 형식 변경?")
    cats: dict[str, list[tuple[str, str]]] = {}
    cur = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        cat = re.match(r"^  (\S[^:]*):\s*(.*)$", line)
        skill = re.match(r"^    - ([a-z0-9-]+):\s*(.*)$", line)
        if skill:
            if cur is None:
                cur = "(uncategorized)"
                cats.setdefault(cur, [])
            cats[cur].append((skill.group(1), skill.group(2).strip()))
        elif cat:
            cur = cat.group(1).strip()
            cats.setdefault(cur, [])
    return cats


def full_description(name: str) -> str | None:
    """렌더 인덱스의 잘린 설명(...) 대신 SKILL.md frontmatter의 전체 description을 찾는다."""
    # DATA는 변수화(HERMES_DATA), /opt/hermes는 Hermes 이미지 고정경로(변수화 대상 아님)
    for root in (Path(f"{DATA}/skills"), Path("/opt/hermes/skills"), Path("/opt/hermes/optional-skills")):
        if not root.exists():
            continue
        for f in root.rglob("SKILL.md"):
            if f.parent.name != name:
                continue
            head = f.read_text(encoding="utf-8", errors="replace")[:2000]
            m = re.search(r'^description:\s*["\x27]?(.+?)["\x27]?\s*$', head, re.M)
            if m:
                return m.group(1).strip()
    return None


def render_catalog(cats: dict) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    total = sum(len(v) for v in cats.values())
    out = [BEGIN, "", f"## 활성 스킬 카탈로그 ({total}개, {now} 기준)",
           "",
           "> 이 섹션은 자동 생성된다. 실제 렌더되는 활성 스킬(disabled/플랫폼 필터 반영)과 1:1.",
           "> 갱신: `docker exec -u 10000 <컨테이너> python3 /opt/data/bin/wiki-skills-sync.py`", ""]
    for cat in sorted(cats):
        if not cats[cat]:
            continue
        out.append(f"### {cat}")
        for name, short in sorted(cats[cat]):
            desc = short
            if short.endswith("..."):
                desc = full_description(name) or short
            if len(desc) > 160:
                desc = desc[:157] + "…"
            out.append(f"- **{name}** — {desc}")
        out.append("")
    out.append(END)
    return "\n".join(out)


def main() -> int:
    cats = parse_index(build_skills_system_prompt())
    catalog = render_catalog(cats)
    txt = WIKI.read_text(encoding="utf-8")
    if BEGIN in txt and END in txt:
        txt = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), catalog, txt, flags=re.S)
    else:
        txt = txt.rstrip() + "\n\n" + catalog + "\n"
    # frontmatter updated 갱신
    txt = re.sub(r"^updated: .*$", f"updated: {datetime.now(KST):%Y-%m-%d}", txt, count=1, flags=re.M)
    with note_lock():
        write_atomic(str(WIKI), txt)
    total = sum(len(v) for v in cats.values())
    print(f"skills-reference.md 카탈로그 갱신: {total}개 스킬, {len(cats)}개 카테고리")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
