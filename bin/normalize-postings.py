#!/usr/bin/env python3
"""postings frontmatter 정규화 — 변칙 스키마를 정본(한글)으로 수렴.

jsc가 만든 노트는 이미 정본이나, jsc 밖 경로(에이전트 직접 생성/편집)가 영문·애드혹 필드로
스키마를 난립시킨다. 이 도구는 그 변칙 노트만 손본다(이미 정본이면 무변경 — churn 방지).

- 동의어 필드명 → 정본 (company→회사 등). 정본이 이미 값 있으면 중복 드롭, 없으면 값 이관.
- `태그`→`tags` 병합. 영문 상태값(applied 등)→한글. 스테일 영문 `priority` 드롭(우선도가 정본).
- completedDate·dateModified·관련 등 매핑 없는 필드는 **보존**. 정본 순서로 재배열.
- 값은 절대 손실 안 함. 기본 DRY-RUN(NORM_APPLY=1일 때만 실제 쓰기). 전체 백업.

emit 형식은 readers(priority-recalc·jsc·note-set-field 정규식)와 호환(스칼라 이중따옴표·리스트 인라인)."""
import glob
import json
import os
import shutil
import sys
from datetime import datetime

try:
    import yaml
except ImportError:
    sys.exit("PyYAML 필요")

DATA = os.environ.get("HERMES_DATA") or "/opt/data"
POSTINGS = f"{DATA}/wiki/automation/job-hunting/postings"
APPLY = os.environ.get("NORM_APPLY") == "1"
BK = f"{DATA}/.backups/{datetime.now().strftime('%Y%m%d')}-fmnorm/postings"

RENAME = {"company": "회사", "position": "포지션", "platform": "보드",
          "status": "상태", "detail": "세부", "applied_date": "지원일"}
DROP = ["priority"]   # 영문 스테일 — 우선도(파생)가 정본
STATUS_VAL = {"applied": "waiting", "completed": "done", "in_progress": "in-progress",
              "rejected": "done", "offer": "in-progress", "found": "open",
              "예정": "open", "발견": "open", "지원완료": "waiting", "진행중": "in-progress",
              "완료": "done", "보류": "done"}
ORDER = ["title", "회사", "포지션", "보드", "적합도", "티어", "우선도", "상태", "세부",
         "게시일", "지원일", "링크", "키워드", "추가링크1", "추가링크2", "추가링크3", "메모", "tags", "private"]
# 코어(항상 존재 보장). 지원일·추가링크3·completedDate·dateModified·관련은 선택(있을 때만).
REQUIRED = ["title", "회사", "포지션", "보드", "적합도", "티어", "우선도", "상태", "세부",
            "게시일", "링크", "키워드", "추가링크1", "추가링크2", "메모", "tags", "private"]


def emit_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    if v is None:
        return '""'
    return json.dumps(str(v), ensure_ascii=False)   # 스칼라 이중따옴표(정규식 readers 호환)


def normalize(fm):
    out = dict(fm)
    ch = False
    if "태그" in out:                      # 태그 → tags 병합
        tg = out.pop("태그"); ch = True
        base = out.get("tags") or []
        base = base if isinstance(base, list) else [base]
        tg = tg if isinstance(tg, list) else [tg]
        out["tags"] = list(dict.fromkeys([str(x) for x in base + tg]))
    for src, dst in RENAME.items():
        if src in out:
            v = out.pop(src); ch = True
            if dst not in out or out.get(dst) in (None, "", "[]"):
                out[dst] = v                # 정본 없으면 이관, 있으면 중복 드롭
    for d in DROP:
        if d in out:
            out.pop(d); ch = True
    st = str(out.get("상태", "")).strip()
    if st in STATUS_VAL:
        out["상태"] = STATUS_VAL[st]; ch = True
    # tags 값 정규화: 영문(job-posting/job-hunting) 제거, 정본 {구직,공고,task} 보장, 나머지(면접·회사명 등) 보존.
    # → Dashboard 태그쿼리(구직/공고)에 전 노트가 잡히게. jsc 밖 경로가 영문 태그로 만든 것을 교정.
    _tg = out.get("tags")
    _tg = _tg if isinstance(_tg, list) else ([_tg] if _tg else [])
    _new = [str(x) for x in _tg if str(x) not in ("job-posting", "job-hunting")]
    for _req in ("구직", "공고", "task"):
        if _req not in _new:
            _new.append(_req)
    if _new != _tg:
        out["tags"] = _new; ch = True
    # 정본 코어 필드 존재 보장(없으면 빈값) — 개수까지 통일. 값 날조 아님(빈 placeholder).
    for k in REQUIRED:
        if k not in out:
            if k == "title":
                out["title"] = (str(out.get("회사", "")) + " · " + str(out.get("포지션", ""))).strip(" ·")
            elif k == "tags":
                out["tags"] = ["구직", "공고", "task"]      # 대시보드 태그쿼리용(기능 필드)
            elif k == "private":
                out["private"] = True                       # 구직 노트는 비공개 기본
            else:
                out[k] = ""
            ch = True
    ordered = {}
    for k in ORDER:
        if k in out:
            ordered[k] = out.pop(k)
    for k in out:                           # 매핑 없는 필드 보존
        ordered[k] = out[k]
    return ordered, ch


def main():
    files = glob.glob(f"{POSTINGS}/*.md")
    sb, sa = set(), set()
    changed = errs = 0
    samples = []
    for fp in files:
        t = open(fp, encoding="utf-8", errors="ignore").read()
        if not t.startswith("---\n"):
            continue
        idx = t.find("\n---", 4)   # 닫는 구분자(줄 시작 ---) — 값 안의 '---'(URL 등)에 안 걸리게
        if idx < 0:
            continue
        raw_fm, body = t[4:idx], t[idx:]   # body는 '\n---\n...' 포함
        try:
            fm = yaml.safe_load(raw_fm) or {}
        except Exception:
            errs += 1; continue
        if not isinstance(fm, dict):
            continue
        sb.add(tuple(sorted(str(k) for k in fm)))
        ordered, ch = normalize(fm)
        sa.add(tuple(sorted(str(k) for k in ordered)))
        if not ch:                          # 이미 정본 — 손대지 않음(churn 방지)
            continue
        newfm = "\n".join(f"{k}: {emit_val(v)}" for k, v in ordered.items())
        newtext = f"---\n{newfm}{body}"   # body는 '\n---\n...'로 시작(닫는 구분자 포함)
        changed += 1
        if len(samples) < 4:
            samples.append((os.path.basename(fp), sorted(str(k) for k in fm), sorted(str(k) for k in ordered)))
        if APPLY:
            os.makedirs(BK, exist_ok=True)
            shutil.copy2(fp, f"{BK}/{os.path.basename(fp)}")
            stt = os.stat(fp); tmp = f"{fp}.{os.getpid()}.tmp"
            open(tmp, "w", encoding="utf-8").write(newtext)
            os.chmod(tmp, stt.st_mode & 0o7777)
            os.replace(tmp, fp)
            os.chown(fp, stt.st_uid, stt.st_gid)
    print(f"{'[적용]' if APPLY else '[DRY-RUN]'} 노트 {len(files)} · 변칙 변경 {changed} · 파싱오류 {errs}")
    print(f"  스키마 종류: {len(sb)} → {len(sa)}")
    for nm, b, a in samples:
        print(f"  · {nm}\n      before({len(b)}): {b}\n      after ({len(a)}): {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
