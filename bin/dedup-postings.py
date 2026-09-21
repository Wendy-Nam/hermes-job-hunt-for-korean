#!/usr/bin/env python3
"""postings 중복 스윕 — norm_key로 묶어 중복을 아카이브(삭제 아님)+링크 병합.

jsc의 find_note를 거친 노트는 이미 norm_key로 dedup된다(공백·하이픈·괄호·법인격 정규화).
이 도구는 그 경로를 '건너뛴' 생성(에이전트 직접 작성 등)이 남긴 변형 중복을 주기적으로 수거한다.
규칙 기반(norm_key) — 퍼지 유사도 매칭 안 함(과거 데이터손실 교훈).

canonical 선택: 사람이 손댄 상태 우선 → 내용 길이 → mtime. 나머지는 백업으로 이동하고,
유니크한 링크는 canonical 본문에 병합한다(비파괴). 회사/포지션 없는 변칙 노트는 불가침(스킵)."""
import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))            # noteio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "job-search"))
from noteio import note_lock, move, write_atomic, DRY_RUN, why_dry  # 공용 잠금·원자성·쓰기스위치
import jobfilter as jf

DATA = os.environ.get("HERMES_DATA") or "/opt/data"
POSTINGS = f"{DATA}/wiki/automation/job-hunting/postings"
BK = f"{DATA}/.backups/{datetime.now().strftime('%Y%m%d')}-dedup/postings"
STATUS_RANK = {"완료": 5, "진행중": 4, "지원완료": 3, "예정": 1, "발견": 0}  # 사람이 손댄 것 우선


def field(text, name):
    m = re.search(r'^' + name + r':\s*"?([^"\n]*)', text, re.M)
    return m.group(1).strip() if m else ""


def merge_link(canon_fp, canon_t, dup_link):
    """dup의 링크가 canonical에 없으면 본문에 병합(비파괴)."""
    if not dup_link or dup_link in canon_t:
        return canon_t
    if "## 병합된 중복 링크" in canon_t:
        canon_t = canon_t.replace("## 병합된 중복 링크\n", f"## 병합된 중복 링크\n- {dup_link}\n", 1)
    else:
        canon_t = canon_t.rstrip() + f"\n\n## 병합된 중복 링크\n- {dup_link}\n"
    write_atomic(canon_fp, canon_t)
    return canon_t


def main():
    if not os.path.isdir(POSTINGS):
        print(f"dedup-postings: {POSTINGS} 없음"); return 0
    groups = {}
    for fp in glob.glob(f"{POSTINGS}/*.md"):
        t = open(fp, encoding="utf-8", errors="ignore").read()
        comp, pos = field(t, "회사"), field(t, "포지션")
        if not comp or not pos:
            continue  # 변칙 노트 불가침
        groups.setdefault(jf.norm_key(comp, pos), []).append([fp, t])

    moved = 0
    with note_lock():                         # 상태갱신·수집 크론과 겹쳐도 안전
        for k, notes in groups.items():
            if len(notes) < 2:
                continue
            notes.sort(key=lambda n: (STATUS_RANK.get(field(n[1], "상태"), 2),
                                      len(n[1]), os.path.getmtime(n[0])), reverse=True)
            canon_fp, canon_t = notes[0]
            for dup_fp, dup_t in notes[1:]:
                canon_t = merge_link(canon_fp, canon_t, field(dup_t, "링크"))
                move(dup_fp, f"{BK}/{os.path.basename(dup_fp)}")   # 아카이브(되돌리기 가능)
                moved += 1
                print(f"  중복 → 백업: {os.path.basename(dup_fp)}  (유지: {os.path.basename(canon_fp)})")
    tag = f" (DRY-RUN, {why_dry()})" if DRY_RUN else ""
    print(f"dedup-postings: 중복 {moved}개 수거{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
