#!/usr/bin/env python3
"""note-set-field.py — 노트 frontmatter의 '지정한 필드만' 안전하게 교체.

Hermes가 지원상태 등을 갱신할 때 frontmatter를 통째로 다시 쓰면 필드순서가 깨지고
링크 등 다른 필드가 유실된다. 이 스크립트는 지정한 필드 라인만 바꾸고 나머지 줄은 그대로 둔다.

정확히 말하면 '바이트 단위 보존'은 아니다(정직하게):
  - 텍스트 모드로 읽고 다시 쓰므로 CRLF는 LF로 정규화될 수 있다
  - mtime은 바뀐다. ACL·확장속성은 보존하지 않는다(권한·소유권은 보존)
  - frontmatter에 같은 키가 중복돼 있으면 첫 번째만 바꾼다(YAML 유효값은 보통 마지막 키다 — 중복 키가 있는 노트는 사람이 먼저 정리할 것)
보장하는 것: 지정하지 않은 필드·본문 내용은 건드리지 않는다, 원자적 교체, 잠금 직렬화.

Usage:
  note-set-field.py <note.md> 세부=지원  (상태는 자동 유도) [메모="2차 면접 7/10"]
  값에 공백/특수문자 있으면 쉘에서 따옴표로 감쌀 것.
규칙: 절대 frontmatter를 통째로 다시 쓰지 말 것. 상태/세부/메모/적합도 갱신은 이 스크립트로만.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noteio import DATA, DRY_RUN, note_lock, why_dry, write_atomic   # 잠금·원자성·쓰기스위치 = 단일 계층


def main():
    if len(sys.argv) < 3 or "=" not in "".join(sys.argv[2:]):
        print("usage: note-set-field.py <note.md> 필드=값 [필드=값 ...]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    # 경로 가드 — 이 도구는 '위키 노트'만 고친다. 에이전트가 외부 입력(메일·공고)에 휘둘려
    # 엉뚱한 경로를 넘겨도 볼트 밖 파일이나 심볼릭 링크 대상을 건드리지 못하게 한다.
    wiki = os.path.realpath(f"{DATA}/wiki")
    real = os.path.realpath(path)
    if os.path.islink(path):
        print(f"거부(심볼릭 링크): {path}", file=sys.stderr); return 1
    if not (real == wiki or real.startswith(wiki + os.sep)):
        print(f"거부(위키 밖 경로): {path}\n  이 도구는 {wiki}/ 아래 노트만 수정한다.", file=sys.stderr); return 1
    if not real.endswith(".md"):
        print(f"거부(.md 아님): {path}", file=sys.stderr); return 1
    pairs = []
    for a in sys.argv[2:]:
        if "=" not in a:
            print(f"무시(형식오류): {a}", file=sys.stderr); continue
        k, v = a.split("=", 1)
        pairs.append((k.strip(), v.strip()))

    # 읽기~쓰기 전체를 잠금 안에서 — 원자적 쓰기만으론 동시 갱신 시 한쪽 변경이 사라진다(lost update).
    lk = note_lock()
    lk.__enter__()

    t = open(path, encoding="utf-8").read()
    m = re.match(r"^(---\n)(.*?)(\n---\n?)(.*)$", t, re.S)
    if not m:
        print("frontmatter 없음 — 중단(안전)", file=sys.stderr); return 1
    head, fm, close, body = m.group(1), m.group(2), m.group(3), m.group(4)
    lines = fm.split("\n")

    changed = []
    for field, value in pairs:
        if not re.fullmatch(r"[\w가-힣_-]+", field):
            print(f"skip(비정상 필드명): {field}", file=sys.stderr); continue
        if "\n" in value or "\r" in value:
            print(f"skip(개행 불가): {field}", file=sys.stderr); continue
        esc = value.replace("\\", "\\\\").replace(chr(34), "\\" + chr(34))
        val = f'"{esc}"'
        pat = re.compile(r"^" + re.escape(field) + r":\s.*$")
        # tags 처럼 리스트/특수 필드는 건드리지 않음(안전)
        if field in ("tags",):
            print(f"skip(보호필드): {field}", file=sys.stderr); continue
        hit = False
        for i, ln in enumerate(lines):
            if pat.match(ln):
                lines[i] = f"{field}: {val}"
                hit = True
                changed.append(field)
                break
        if not hit:                     # 없으면 frontmatter 끝에 추가(순서·기존필드 보존)
            lines.append(f"{field}: {val}")
            changed.append(field + "(추가)")

    if DRY_RUN:
        lk.__exit__(None, None, None)
        print(f"[DRY-RUN] {path} · 변경했을 필드 {changed} — {why_dry()}")
        return 0
    write_atomic(path, head + "\n".join(lines) + close + body)
    lk.__exit__(None, None, None)    # 잠금 해제(교체 완료 후)
    print(f"OK: {path} · 변경 {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
