"""Queue mechanics for autonomous-triggers: cooldowns + enqueue + the
check_triggers() orchestration that ties state.py (world state) and
triggers.py (condition/action table) together.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from state import read_state
from triggers import TRIGGERS, render

_LOG = logging.getLogger(__name__)

_DATA = Path(os.environ.get("HERMES_DATA", "/opt/data"))
QUEUE_DIR = _DATA / "autonomous-queue"
DONE_DIR = QUEUE_DIR / "done"


def _cooldown_path(trigger_id: str) -> Path:
    return QUEUE_DIR / f".{trigger_id}.cooldown"


def is_cooldown_active(trigger_id: str) -> bool:
    fp = _cooldown_path(trigger_id)
    if not fp.exists():
        return False
    try:
        trig = next(t for t in TRIGGERS if t["id"] == trigger_id)
        return (time.time() - float(fp.read_text().strip())) < trig["cooldown_hours"] * 3600
    except Exception:
        return False


def _set_cooldown(trigger_id: str) -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    _cooldown_path(trigger_id).write_text(str(time.time()))


def enqueue(trigger: dict, state: dict) -> Path:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    channel, prompt = render(trigger, state)
    item = {
        "trigger_id": trigger["id"], "action_id": trigger["action"],
        "prompt": prompt, "channel": channel, "priority": trigger["priority"],
        "created_at": datetime.now().isoformat(),
    }
    fp = QUEUE_DIR / f"{trigger['id']}_{int(time.time() * 1000)}.json"
    fp.write_text(json.dumps(item, ensure_ascii=False, indent=2))
    _set_cooldown(trigger["id"])
    _LOG.info("autonomous trigger fired: %s -> %s", trigger["id"], trigger["action"])
    return fp


def check_triggers(state: dict | None = None) -> list[dict]:
    state = state if state is not None else read_state()
    fired = []
    for trigger in TRIGGERS:
        if is_cooldown_active(trigger["id"]):
            continue
        try:
            if trigger["condition"](state):
                enqueue(trigger, state)
                fired.append(trigger)
        except Exception as e:
            _LOG.warning("autonomous trigger %s failed: %s", trigger["id"], e)
    return fired
