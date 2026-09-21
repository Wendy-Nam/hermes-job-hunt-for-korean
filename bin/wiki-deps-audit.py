#!/usr/bin/env python3
"""위키 링크·크론 참조·스킬 의존성 전수 감사 (VPS 호스트/컨테이너 양쪽 지원)."""
import json
import os
import re
import subprocess

# 컨테이너 안(/opt/data 존재)과 호스트(/docker/... 마운트) 모두에서 동작.
# HERMES_DATA를 명시하면 그 경로로 격리(테스트·호스트) — 이때 컨테이너 전용 명령 경로를 타지 않는다.
DATA = os.environ.get("HERMES_DATA") or "/opt/data"
IN_CONTAINER = os.path.isdir("/opt/data/wiki") and not os.environ.get("HERMES_DATA")
W = f"{DATA}/wiki"
failures = 0

# 1) 위키 전체 [[링크]] 해석 검사
real, alltext = [], {}
for root, dirs, files in os.walk(W):
    dirs[:] = [d for d in dirs if d not in (".stversions", ".obsidian", ".stfolder", ".trash", "TaskNotes")]
    for f in files:
        if f.endswith(".md") and ".bak" not in f:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, W)
            real.append(rel)
            alltext[rel] = open(p, encoding="utf-8", errors="replace").read()
bases = {os.path.splitext(os.path.basename(r))[0] for r in real}
paths = set(real)  # 루트 기준 상대경로(.md 포함) — 경로형 위키링크 해석용
attachments = set()
for root, _dirs, files in os.walk(os.path.join(W, "raw", "attachments")):
    attachments.update(files)
dead = {}
for rel, txt in alltext.items():
    if rel.startswith("templates/"):
        continue  # 템플릿 플레이스홀더 [[...]]는 링크가 아님
    txt = re.sub(r"```.*?```", "", txt, flags=re.S)  # 펜스드 코드블록 제외
    txt = re.sub(r"`[^`\n]*`", "", txt)  # 백틱 안의 문법 예시 제외
    for link in re.findall(r"\[\[([^\]|#]+)", txt):
        link = link.strip()
        if not link or link in bases or link in attachments:
            continue
        # 경로형 링크 해석: 루트 기준 → 소스파일 기준 상대 → 폴더 링크
        cand = link if link.endswith(".md") else link + ".md"
        if cand in paths:
            continue
        if os.path.normpath(os.path.join(os.path.dirname(rel), cand)) in paths:
            continue
        if os.path.isdir(os.path.join(W, link)):
            continue
        dead.setdefault(rel, set()).add(link)
print("== 위키 전체 죽은 링크:")
for k in sorted(dead):
    print("   %s -> %s" % (k, sorted(dead[k])))
    failures += len(dead[k])
if not dead:
    print("   0건")

# 2) 크론 잡 참조 경로/스크립트 실존
jobs = json.load(open(f"{DATA}/cron/jobs.json"))["jobs"]
print("== 크론 참조 경로:")
miss = 0
for j in jobs:
    refs = set(re.findall(r"/opt/data/[\w/.\-가-힣]+\.(?:md|py|sh|json)", j.get("prompt") or ""))
    if j.get("script"):
        refs.add("/opt/data/scripts/" + j["script"])
    for r in sorted(refs):
        if re.search(r"YYYY|MM-DD|<[^>]+>|\*", r):  # 템플릿 자리표시자 스킵
            continue
        host = r if IN_CONTAINER else r.replace("/opt/data", DATA)
        if not os.path.exists(host):
            miss += 1
            print("   MISSING [%s]: %s" % (j["name"], r))
print("   누락 %d건" % miss)
failures += miss

# 3) job-search 스킬 related_skills 실존 (+ 컨테이너 baked 스킬 포함)
FIND_SKILLS = "find /opt/data/skills /opt/hermes/skills /opt/hermes/optional-skills -name SKILL.md 2>/dev/null"
cmd = ["sh", "-c", FIND_SKILLS] if IN_CONTAINER else \
      ["docker", "exec", os.environ.get("HERMES_CONTAINER", "hermes-agent"), "sh", "-c", FIND_SKILLS]
try:
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
except FileNotFoundError:
    out = ""
    print("   (docker 미설치 — 컨테이너 baked 스킬 검사 생략)")
allsk = {os.path.basename(os.path.dirname(l)) for l in out.splitlines()}
print("== related_skills 검사 (job-search):")
bad = 0
sk_local = subprocess.run(["find", f"{DATA}/skills/job-search", "-name", "SKILL.md"],
                          capture_output=True, text=True).stdout
for l in sk_local.splitlines():
    head = open(l, encoding="utf-8").read()[:1500]
    m = re.search(r"related_skills:\s*\[([^\]]*)\]", head)
    if not m:
        continue
    for rs in [x.strip() for x in m.group(1).split(",") if x.strip()]:
        name = rs.split(":")[-1]
        if name not in allsk:
            bad += 1
            print("   BAD %s -> %s" % (os.path.basename(os.path.dirname(l)), rs))
print("   불량 %d건" % bad)
failures += bad


# ── 위키 중복단락 검사 (sprawl 감시) ──
def _dup_paragraphs():
    import hashlib, glob
    seen = {}
    dups = 0
    for p in glob.glob(W + "/**/*.md", recursive=True):
        if "/.stversions/" in p or "/templates/" in p or "/postings/" in p:
            continue
        try:
            txt = open(p, encoding="utf-8").read()
        except OSError:
            continue
        for para in txt.split("\n\n"):
            norm = re.sub(r"\s+", " ", para).strip()
            if len(norm) < 80:
                continue
            h = hashlib.md5(norm.encode()).hexdigest()
            if h in seen and seen[h] != p:
                print("   DUP %s <-> %s :: %s..." % (p.split("wiki/")[-1], seen[h].split("wiki/")[-1], norm[:50]))
                dups += 1
            else:
                seen[h] = p
    print("== 위키 중복단락(80자+):", dups, "건")
try:
    _dup_paragraphs()
except Exception as _e:
    print("중복단락 검사 스킵:", _e)

print("TOTAL FAILURES: %d" % failures)
raise SystemExit(1 if failures else 0)
