#!/usr/bin/env python3
"""_expand_soul_imports 가드 강화: 파일당 8k·총량 20k 초과 시 포인터 강등+경고 로그."""
import re, sys

TARGET = "/opt/hermes/agent/prompt_builder.py"
NEW = '''def _expand_soul_imports(content, _max_total=20000, _max_file=8000):
    """Expand `@import /abs/path` lines in SOUL.md (single level, HERMES_HOME only).

    토큰 가드: 파일당 _max_file자, 총 _max_total자 초과분은 확장하지 않고
    @import 줄을 그대로 남긴다(포인터 강등) + 경고 로그. 매턴 프롬프트라
    조용한 폭발이 없어야 한다.
    """
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
                    if t and len(t) <= _max_file and total + len(t) <= _max_total:
                        total += len(t)
                        out.append(t)
                        continue
                    if t:
                        logger.warning(
                            "SOUL @import 크기 초과 — 포인터 강등: %s (%d자, 파일한도 %d·총한도 %d)",
                            rp, len(t), _max_file, _max_total)
            except Exception:
                pass
        out.append(line)
    return "\\n".join(out)'''

src = open(TARGET, encoding="utf-8").read()
m = re.search(r"def _expand_soul_imports\(.*?\n    return \"\\\\n\"\.join\(out\)", src, re.S)
if not m:
    m = re.search(r'def _expand_soul_imports\(.*?\n    return "\\n"\.join\(out\)', src, re.S)
assert m, "helper block not found"
if "_max_file=8000" in src:
    print("already hardened — noop")
    sys.exit(0)
src = src[:m.start()] + NEW + src[m.end():]
import py_compile, tempfile, os, shutil
tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
tmp.write(src)
tmp.close()
py_compile.compile(tmp.name, doraise=True)
shutil.copystat(TARGET, tmp.name)
os.replace(tmp.name, TARGET)
print("hardened OK")
