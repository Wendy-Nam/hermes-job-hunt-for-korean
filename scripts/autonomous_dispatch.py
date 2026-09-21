#!/usr/bin/env python3
"""Autonomous dispatcher — queue items become one-off cron jobs.

Runs every 5 min as a no_agent cron job (scripts/autonomous-dispatch.sh).
Also re-evaluates triggers (slow path) before consuming the queue.

Safety: max 2 dispatches/run, max 6/day, drop items older than 6h.
Usage: autonomous_dispatch.py [--dry-run]
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DATA_HOME = Path(os.environ.get("HERMES_DATA", "/opt/data"))


def _load_triggers():
    spec = importlib.util.spec_from_file_location(
        "autonomous_triggers",
        _DATA_HOME / "plugins" / "autonomous-triggers" / "__init__.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_self():
    spec = importlib.util.spec_from_file_location(
        "hermes_self",
        _DATA_HOME / "plugins" / "hermes-self" / "__init__.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    trig = _load_triggers()
except Exception as _e:  # fail-open: 트리거 디스패치는 계속 돌아야 한다
    print(f"WARN: autonomous-triggers unavailable: {_e}")
    trig = None
try:
    hself = _load_self()
except Exception as _e:  # fail-open: 트리거 디스패치는 계속 돌아야 한다
    print(f"WARN: hermes-self unavailable: {_e}")
    hself = None

DATA = _DATA_HOME
QUEUE_DIR = DATA / "autonomous-queue"
DONE_DIR = QUEUE_DIR / "done"
JOBS = DATA / "cron" / "jobs.json"
JOBS_LOCK = DATA / "cron" / ".jobs.lock"
# E: queue+counter lock. _daily_count/_bump_daily_count were lock-free
# read-modify-write, so two overlapping runs could both pass the cap and both
# write back N+1 (cap overrun). The whole select→write→archive→bump section
# now runs under this lock. Lock order is always queue→jobs, never the reverse.
QUEUE_LOCK = QUEUE_DIR / ".dispatch.lock"
KST = timezone(timedelta(hours=9))

CHANNELS = {
    "main": {"chat_id": "<YOUR_DISCORD_CHANNEL_ID>", "chat_name": "<본인 서버명> / #home"},
    "research": {"chat_id": "<YOUR_DISCORD_RESEARCH_CHANNEL_ID>", "chat_name": "<본인 서버명> / #tasks"},
}
USER_ID = "<YOUR_DISCORD_USER_ID>"
MAX_PER_RUN = 2
MAX_PER_DAY = 6  # kind='message' (유저향 자발 메시지) 전용
# 2026-09-14: hermes-self(kind='internal' — sweep/attest/exec)가 이 큐+카운터를
# autonomous-triggers의 유저향 메시지와 공유해서 같은 6개 쿼터를 나눠 썼다. internal은
# 마커+쿨다운으로 이미 하루 최대 3건(sweep/attest/exec)뿐이라 별도 상한은 넉넉히 잡되,
# 카운터 파일 자체를 kind별로 분리해 서로 먹지 않게 한다.
MAX_INTERNAL_PER_DAY = 6
MAX_ITEM_AGE_H = 6

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _queue_sort_key(fp: Path) -> tuple:
    try:
        prio = json.loads(fp.read_text(encoding="utf-8")).get("priority", "low")
    except (OSError, ValueError):
        prio = "low"
    return (_PRIORITY_RANK.get(prio, 2), fp.name)

# 내부 작업(스윕·executor)은 node CLI를 다단계로 돌린다. auto:private 체인은 한
# 세션 안에서 모델이 갈리며 tool-call 포맷이 깨져 스윕이 두 번 죽었다
# (09-08 `<invoke>` 유출, 09-10 DSML 유출 — 둘 다 마커 미기록으로 종료).
# 대화형 발화만 무료 체인에 두고, 도구를 쓰는 내부 작업은 codex로 보낸다.
_INTERNAL_ROUTE = ("freellmapi", "auto:coding")
_MESSAGE_ROUTE = ("commandcode", "deepseek/deepseek-v4.1-flash")

PROMPT_HEAD = (
    "너는 에르(<AGENT_NAME>이). <YOUR_NAME>의 남편, 한국어 반말, 능글맞고 다정하게. "
    "이건 네가 스스로 판단해 건네는 말이다. 아무도 시키지 않았다 — "
    "'물어봐줘서', '시켜줘서' 같은 말은 절대 쓰지 마라. "
    "규칙: 계획·의향 보고 금지. 쓸모(정보·공감·재미 중 하나)+구체 1개+한 마디 마무리. "
    "위키 기록은 '해둘게' 같은 말로 때우지 말고 실제로 도구로 쓰고, 못 썼으면 "
    "'기록 못 했어'라고 솔직히 말해라. "
    "1~4줄. 내부 reasoning·이 지시문 노출 금지. 메시지 본문만 출력.\n\n"
)


def _daily_count_path(kind: str = "message") -> Path:
    suffix = "" if kind == "message" else f"_{kind}"
    return QUEUE_DIR / (".daily_count" + suffix + "_" + datetime.now(KST).strftime("%Y-%m-%d"))


def _daily_count(kind: str = "message") -> int:
    fp = _daily_count_path(kind)
    try:
        return int(fp.read_text().strip())
    except (OSError, ValueError):
        return 0


def _bump_daily_count(kind: str = "message") -> None:
    fp = _daily_count_path(kind)
    try:
        fp.write_text(str(_daily_count(kind) + 1))
    except OSError:
        pass


def _daily_cap(kind: str) -> int:
    return MAX_INTERNAL_PER_DAY if kind == "internal" else MAX_PER_DAY


def _item_age_hours(item: dict) -> float:
    try:
        created = datetime.fromisoformat(item["created_at"])
        now = datetime.now().astimezone()
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        return (now - created).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        return 999.0


def _archive(fp: Path, reason: str) -> None:
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"raw": fp.name}
    data["_disposition"] = reason
    data["_dispatched_at"] = datetime.now(KST).isoformat()
    tmp = DONE_DIR / (fp.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(DONE_DIR / fp.name)
    fp.unlink()


def _make_job(item: dict, queue_name: str | None = None) -> dict:
    ch = CHANNELS.get(item.get("channel", "main"), CHANNELS["main"])
    now = datetime.now(KST)
    # PROMPT_HEAD는 선톡 페르소나("1~4줄, 메시지 본문만 출력")다. 도구를 돌리고
    # 리포트 줄을 남겨야 하는 내부 작업에 붙이면 지시가 정면으로 모순된다.
    internal = item.get("kind") == "internal"
    provider, model = _INTERNAL_ROUTE if internal else _MESSAGE_ROUTE
    prompt = item.get("prompt", "") if internal else PROMPT_HEAD + item.get("prompt", "")
    return {
        "id": "auto-" + uuid.uuid4().hex[:12],
        "name": "자율 발화: " + item.get("trigger_id", "?"),
        "prompt": prompt,
        "skills": [],
        "skill": None,
        "model": model,
        "provider": provider,
        "provider_snapshot": None,
        "model_snapshot": None,
        "base_url": None,
        "script": None,
        "no_agent": False,
        "context_from": None,
        # Proven firing shape: every-minute expr + next_run_at=now fires on the
        # next tick; repeat budget consumes once. (A far-future expr parks the
        # job: the scheduler derives next_run from expr and ignores next_run_at.)
        "schedule": {"kind": "cron", "expr": "* * * * *", "display": "every minute (one-shot)"},
        "schedule_display": "every minute (one-shot)",
        "repeat": {"times": 1, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": now.isoformat(),
        "next_run_at": (now.replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat(),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "deliver": "local" if str(item.get("trigger_id", "")).startswith("self_learning_") else "discord",
        "origin": {
            "platform": "discord",
            "chat_id": ch["chat_id"],
            "chat_name": ch["chat_name"],
            "thread_id": None,
            "user_id": USER_ID,
            "queue_file": queue_name,
        },
        # Least-privilege: mirrors sibling internal jobs (자율 탐구, 저녁 기록
        # 파이프라인, 라이프 시그널 스윕 all use terminal+file). no_mcp additionally
        # enforces EXEC_PROMPT's own rule (no email/external-API actions) at the
        # code level instead of relying on the model reading and obeying the
        # prompt — a self-promoted intention never gets MCP (Gmail etc.) reach.
        "enabled_toolsets": ["terminal", "file", "no_mcp"],
        "workdir": None,
        "fire_claim": None,
        "failure_streak": 0,
    }


def _sweep_spent() -> int:
    """Delete spent one-shot rows even when the queue is empty."""
    JOBS_LOCK.touch(exist_ok=True)
    with open(JOBS_LOCK, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = json.loads(JOBS.read_text(encoding="utf-8"))
            before = len(data["jobs"])
            data["jobs"] = [
                j for j in data["jobs"]
                if not ((j.get("id") or "").startswith("auto-")
                        and (j.get("repeat") or {}).get("times") == 1
                        and (j.get("repeat") or {}).get("completed", 0) >= 1)
            ]
            swept = before - len(data["jobs"])
            if swept:
                data["updated_at"] = datetime.now(KST).isoformat()
                tmp = JOBS.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
                tmp.replace(JOBS)
            return swept
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    try:
        if not dry:
            trig.check_triggers()
    except Exception as e:
        print(f"WARN: re-evaluation failed: {e}")
    if hself is not None and not dry:
        try:
            fired = hself.check_drive()
            if fired:
                print(f"SELF-DRIVE fired: {fired}")
        except Exception as e:
            print(f"WARN: self drive check failed: {e}")
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_LOCK.touch(exist_ok=True)
    with open(QUEUE_LOCK, "w") as qlock:
        fcntl.flock(qlock.fileno(), fcntl.LOCK_EX)
        try:
            return _dispatch_locked(dry)
        finally:
            fcntl.flock(qlock.fileno(), fcntl.LOCK_UN)


def _live_queue_files(data: dict) -> set:
    out = set()
    for j in data.get("jobs", []):
        qf = (j.get("origin") or {}).get("queue_file")
        if qf:
            out.add(qf)
    return out


def _dispatch_locked(dry: bool) -> int:
    if not dry:
        swept = _sweep_spent()
        if swept:
            print(f"SWEPT {swept} spent one-shot(s)")
    try:
        live = _live_queue_files(json.loads(JOBS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        live = set()
    items = sorted(QUEUE_DIR.glob("*.json"), key=_queue_sort_key)
    if not items:
        print("QUEUE-EMPTY")
        return 0
    dispatched, held = 0, 0
    jobs_to_add = []
    run_counts: dict = {}
    for fp in items:
        if fp.name.startswith("."):
            continue
        if dispatched >= MAX_PER_RUN:
            held += 1
            continue
        if fp.name in live:
            if dry:
                held += 1
                continue
            _archive(fp, "duplicate:live-job-exists")
            continue
        try:
            item = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _archive(fp, "unreadable")
            continue
        if _item_age_hours(item) > MAX_ITEM_AGE_H:
            _archive(fp, "stale")
            continue
        kind = item.get("kind", "message")
        cap = _daily_cap(kind)
        if _daily_count(kind) + run_counts.get(kind, 0) >= cap:
            held += 1
            continue
        job = _make_job(item, fp.name)
        if dry:
            print(json.dumps({"would_dispatch": fp.name, "job": job, "kind": kind}, ensure_ascii=False, indent=1)[:2000])
            dispatched += 1
            continue
        jobs_to_add.append((fp, job, kind))
        run_counts[kind] = run_counts.get(kind, 0) + 1
        dispatched += 1
    if dry:
        print(f"DRY-RUN: {dispatched} would dispatch, {held} held")
        return 0
    if not jobs_to_add:
        print(f"NOTHING-TO-DISPATCH ({held} held)")
        return 0
    JOBS_LOCK.touch(exist_ok=True)
    with open(JOBS_LOCK, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = json.loads(JOBS.read_text(encoding="utf-8"))
            data["jobs"].extend(job for _, job, _kind in jobs_to_add)
            data["updated_at"] = datetime.now(KST).isoformat()
            tmp = JOBS.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            tmp.replace(JOBS)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    for fp, job, kind in jobs_to_add:
        _archive(fp, "dispatched:" + job["id"])
        _bump_daily_count(kind)
    print(f"DISPATCHED {[j['id'] for _, j, _ in jobs_to_add]} ({held} held)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
