#!/usr/bin/env python3
"""오래 방치된 발견 노트 정리 — 상태=발견 & 적합도 빈 & mtime 14일+ 수거.
삭제가 아니라 .backups/<날짜>/stale-postings/로 이동한다(수집기 reap_rejected와 같은 관례 — 되돌리기 가능).
장부(.job-seen.json)는 유지하므로 재스크랩해도 다시 안 생김."""
import os, re, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from noteio import note_lock, move       # 노트 이동도 공용 잠금 계층으로
DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
P = f"{DATA}/wiki/automation/job-hunting/postings"
BK = f"{DATA}/.backups/{datetime.now().strftime('%Y%m%d')}/stale-postings"
cutoff = time.time() - 14*86400
n = 0
with note_lock():                        # 상태 갱신·수집 크론과 겹쳐도 안전하게
  for fn in os.listdir(P) if os.path.isdir(P) else []:
      if not fn.endswith(".md"): continue
      fp = f"{P}/{fn}"
      if os.path.getmtime(fp) > cutoff: continue
      head = open(fp, encoding="utf-8").read()[:400]
      st = re.search(r"세부:\s*\"?([^\"\n]*)", head)
      fit = re.search(r"적합도:\s*\"?([^\"\n]*)", head)
      if st and st.group(1).strip()=="발견" and (not fit or not fit.group(1).strip()):
          move(fp, f"{BK}/{fn}"); n += 1   # move가 대상 디렉터리를 만든다(DRY-RUN이면 아무것도 안 함)
print(f"job-notes-cleanup: 방치 발견노트 {n}개 수거 → {BK} (장부 유지, 되돌리기 가능)")
