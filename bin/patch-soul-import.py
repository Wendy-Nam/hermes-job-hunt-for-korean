#!/usr/bin/env python3
"""SOUL.md @import 확장 패치 (멱등).

/opt/hermes/agent/prompt_builder.py 의 load_soul_md 에 `@import <절대경로>` 한 줄
확장을 추가한다. SOUL.md를 모듈형 룰북(wiki/hermes/rules/*.md)으로 쪼개도
매 턴 시스템 프롬프트에는 전문이 들어가게 하는 패치.

- 이미지 레이어 패치라 컨테이너 **재생성** 시 소멸 → 재실행 필요:
    docker exec hermes-agent-ywj7-hermes-agent-1 python3 /opt/data/bin/patch-soul-import.py
  (restart 는 보존됨)
- fail-open: 대상 파일이 없거나 /opt/data 밖이면 @import 줄을 그대로 둔다
  (모델에게 경로 포인터로 보임 = 종전 동작).
"""
import sys

TARGET = "/opt/hermes/agent/prompt_builder.py"
MARKER = "def _expand_soul_imports("

HELPER = '''
def _expand_soul_imports(content, _max_total=40000):
    """Expand `@import /abs/path` lines in SOUL.md (single level, HERMES_HOME only)."""
    import re as _re
    try:
        home = str(get_hermes_home().resolve())
    except Exception:
        return content
    out, total = [], 0
    for line in content.splitlines():
        m = _re.match(r"^@import\\s+(\\S+)\\s*$", line)
        if m:
            try:
                rp = Path(m.group(1)).resolve()
                if str(rp).startswith(home) and rp.is_file():
                    t = rp.read_text(encoding="utf-8").strip()
                    if t and total + len(t) <= _max_total:
                        total += len(t)
                        out.append(t)
                        continue
            except Exception:
                pass
        out.append(line)
    return "\\n".join(out)

'''

CALL_ANCHOR = '        content = _scan_context_content(content, "SOUL.md")'
CALL_LINE = '        content = _expand_soul_imports(content)\n'
DEF_ANCHOR = "def load_soul_md("


def main():
    src = open(TARGET, encoding="utf-8").read()
    if MARKER in src:
        print("already patched — noop")
        return 0
    if DEF_ANCHOR not in src or CALL_ANCHOR not in src:
        print("ERROR: anchors not found; upstream changed — patch manually", file=sys.stderr)
        return 1
    src = src.replace(DEF_ANCHOR, HELPER.lstrip("\n") + "\n" + DEF_ANCHOR, 1)
    # load_soul_md 안의 scan 호출 직전에 확장 호출 삽입 (첫 번째 발생 = load_soul_md 내부)
    src = src.replace(CALL_ANCHOR, CALL_LINE + CALL_ANCHOR, 1)
    import py_compile, tempfile, os, shutil
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    tmp.write(src)
    tmp.close()
    py_compile.compile(tmp.name, doraise=True)
    shutil.copystat(TARGET, tmp.name)
    os.replace(tmp.name, TARGET)
    print("patched OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
