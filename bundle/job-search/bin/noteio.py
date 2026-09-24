#!/usr/bin/env python3
"""noteio.py — 노트 파일의 읽기·수정·이동을 위한 단일 잠금/원자적 저장 계층.

왜 있나: 노트를 건드리는 주체가 여럿이다(크론 수집기·판정 커밋·우선도 재계산·정리·대화 중 상태 갱신).
각자 `open(p,"w")`로 직접 쓰면 ①도중에 죽으면 반쯤 쓰인 노트가 남고 ②동시에 쓰면 한쪽 변경이
통째로 사라진다(lost update). 그래서 노트를 바꾸는 모든 경로는 이 모듈을 거친다.

규칙:
  - 쓰기/이동은 note_lock() 안에서. 락은 잡 상태 파일과 같은 것(.jsc.lock)을 쓴다 — 판정 커밋과
    노트 편집이 서로를 덮지 않게 하려면 같은 락이어야 한다.
  - 저장은 임시파일 → fsync → os.replace. 권한·소유권은 원본 그대로 유지
    (600 노트가 644로 풀리거나, root로 실행됐을 때 소유권이 바뀌는 것 방지).
  - HERMES_KIT_DRY_RUN=1이면 쓰기/이동을 코드 레벨에서 차단한다.
"""
import fcntl
import os
import shutil
import tempfile
from contextlib import contextmanager

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
LOCK_PATH = f"{DATA}/.jsc.lock" if os.path.isdir(DATA) else os.path.join(tempfile.gettempdir(), "hermes-kit.lock")

# ── 쓰기 스위치: 기본은 '안 쓴다'(DRY-RUN). ────────────────────────────────────
# 프롬프트로 "DRY-RUN 하라"고 시키는 방식은 기본값이 아니다 — 모델이 지시를 흘리면 그대로 써버린다.
# 그래서 코드의 기본을 읽기 전용으로 두고, 사람이 **명시적으로 스위치를 켤 때만** 쓴다:
#   ① 마커 파일:  touch $DATA/.kit-live      (재시작 불필요 — 운영 중 전환용)
#   ② 환경변수:   HERMES_KIT_LIVE=1
# 켠 뒤에도 HERMES_KIT_DRY_RUN=1을 주면 그 실행만 다시 읽기 전용이 된다(테스트·점검용).
LIVE_MARKER = f"{DATA}/.kit-live"
LIVE = (os.environ.get("HERMES_KIT_LIVE", "").lower() in ("1", "true", "yes")
        or os.path.exists(LIVE_MARKER))
DRY_RUN = (not LIVE) or os.environ.get("HERMES_KIT_DRY_RUN", "").lower() in ("1", "true", "yes")


def why_dry():
    """왜 안 쓰는지 한 줄로 — 조용히 아무것도 안 하는 것처럼 보이지 않게."""
    if not LIVE:
        return f"쓰기 스위치 꺼짐(기본값) — 켜려면: touch {LIVE_MARKER}  또는 HERMES_KIT_LIVE=1"
    return "HERMES_KIT_DRY_RUN=1 — 이번 실행만 읽기 전용"


_HELD_ENV = "HERMES_KIT_LOCK_HELD"


@contextmanager
def note_lock():
    """노트·잡 상태 파일 공용 배타 락.

    부모가 이미 쥔 채로 자식 프로세스를 부르는 경로가 있다(jsc가 락 안에서 priority-recalc 실행).
    flock은 프로세스 단위라 자식이 같은 락을 다시 잡으면 영원히 대기한다 → 환경변수로 보유 사실을
    자식에게 물려주고, 물려받은 쪽은 다시 잡지 않는다(이미 직렬화돼 있으므로 안전).
    """
    if os.environ.get(_HELD_ENV) == "1":
        yield                       # 부모가 이미 보유 — 교착 방지
        return
    lk = open(LOCK_PATH, "a")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX)
        os.environ[_HELD_ENV] = "1"     # 자식 프로세스가 상속
        yield
    finally:
        os.environ.pop(_HELD_ENV, None)
        lk.close()          # close가 flock도 해제


def write_atomic(path, text):
    """임시파일 → fsync → os.replace. 중단돼도 원본은 온전하다. 권한·소유권 보존."""
    if DRY_RUN:
        print(f"[DRY-RUN] 쓰기 생략: {path}")
        return
    d, base = os.path.dirname(path) or ".", os.path.basename(path)
    st = os.stat(path) if os.path.exists(path) else None
    tmp = os.path.join(d, f".{base}.{os.getpid()}.tmp")   # 같은 디렉터리라야 replace가 원자적
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if st:
        os.chmod(tmp, st.st_mode & 0o7777)      # 기존 파일: 권한 그대로
        try:
            os.chown(tmp, st.st_uid, st.st_gid)  # 소유권도 그대로(root 실행 시 바뀌는 것 방지)
        except PermissionError:
            pass
    else:
        os.chmod(tmp, 0o600)      # 새 파일: 지원이력·면접·메일 요약이 들어가니 기본 비공개
    os.replace(tmp, path)


def move(src, dst):
    """노트 이동(수거·아카이브). 대상 디렉터리는 알아서 만든다."""
    if DRY_RUN:
        print(f"[DRY-RUN] 이동 생략: {os.path.basename(src)} → {os.path.dirname(dst)}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)


def demo():
    """자체 검사: python3 noteio.py"""
    import json
    d = tempfile.mkdtemp()
    p = os.path.join(d, "n.md")
    open(p, "w").write("orig")
    os.chmod(p, 0o600)
    with note_lock():
        write_atomic(p, "new")
    assert open(p).read() == "new", "쓰기 실패"
    assert oct(os.stat(p).st_mode & 0o777) == "0o600", "권한 유실"
    assert not [f for f in os.listdir(d) if f.endswith(".tmp")], "임시파일 잔존"
    with note_lock():
        move(p, os.path.join(d, "bak", "n.md"))
    assert os.path.exists(os.path.join(d, "bak", "n.md")) and not os.path.exists(p), "이동 실패"
    shutil.rmtree(d)
    print("noteio 자체검사 OK (원자성·권한보존·임시파일정리·이동)")


if __name__ == "__main__":
    demo()
