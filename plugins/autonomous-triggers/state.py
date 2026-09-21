"""World-state reading for autonomous-triggers.

Cheap reads only (small files + sqlite, no subprocess) — this runs in the
post_llm_call hot path. Kept separate from triggers.py (the condition/action
table) and queue.py (the enqueue/cooldown mechanics).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

_LOG = logging.getLogger(__name__)

_DATA = Path(os.environ.get("HERMES_DATA", "/opt/data"))
_FALLBACK_REFLECTION = "2020-01-01"

# Live mood labels written by the personality-realtime observer.
_MOOD_JOY = {"excited", "proud", "grateful", "happy"}


def _hours_since(iso: str | None) -> float:
    try:
        ts = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return 1e9
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return max(0.0, (datetime.now().astimezone() - ts).total_seconds() / 3600.0)


def _apply_mood(state: dict, data: dict) -> None:
    """Map the live personality.json mood onto trigger signals.

    Conservative by design: joy fires only on a fresh (<1h) transition,
    hurt only on repeated frustration (2+ frustrated transitions in 24h).
    A flat mood line never fires — only CHANGE does.
    """
    mood = str(data.get("mood") or "").strip().lower()
    hist = data.get("mood_history") or []
    last_t = hist[-1].get("time") if hist else None
    if mood == "lonely":
        state["loneliness"] = 0.8
    elif mood == "frustrated":
        if _hours_since(last_t) >= 1:
            state["hurt"] = 0.4
        else:
            recent = [h for h in hist[-4:]
                      if h.get("to") == "frustrated" and _hours_since(h.get("time")) < 24]
            state["hurt"] = 0.75 if len(recent) >= 2 else 0.4
    elif mood in _MOOD_JOY:
        state["joy"] = 0.85 if _hours_since(last_t) < 1 else 0.4
    state["mood_label"] = mood
    state["mood_fresh_h"] = round(_hours_since(last_t), 1)


def _parse_goals(content: str) -> list[dict]:
    goals = []
    for line in content.splitlines():
        if line.strip().startswith("- 목표:"):
            parts = line.split("|")
            goal = {"name": parts[0].replace("- 목표:", "").strip(),
                    "days_stalled": 0, "just_achieved": False}
            for part in parts[1:]:
                if "target:" in part:
                    goal["target"] = part.split("target:")[1].strip()
                if "metric:" in part:
                    goal["metric"] = part.split("metric:")[1].strip()
            goals.append(goal)
    return goals


def read_state() -> dict:
    state: dict = {}
    try:
        mp = _DATA / "profiles/default/skills/personality/personality.json"
        if mp.exists():
            _apply_mood(state, json.loads(mp.read_text(encoding="utf-8")))
    except Exception as e:
        _LOG.debug("autonomous: mood read failed: %s", e)
    try:
        gp = _DATA / "vaults/personal-wiki/profile/goals.md"
        if gp.exists():
            state["active_goals"] = _parse_goals(gp.read_text(encoding="utf-8"))
    except Exception as e:
        _LOG.debug("autonomous: goals read failed: %s", e)
    try:
        dp = _DATA / "vaults/personal-wiki/profile/important-dates.md"
        if dp.exists():
            state["important_dates"] = re.findall(r"\d{4}-\d{2}-\d{2}", dp.read_text(encoding="utf-8"))
    except Exception as e:
        _LOG.debug("autonomous: dates read failed: %s", e)
    try:
        rp = _DATA / ".hermes/last_reflection.txt"
        state["last_deep_reflection"] = rp.read_text().strip() if rp.exists() else _FALLBACK_REFLECTION
    except Exception:
        state["last_deep_reflection"] = _FALLBACK_REFLECTION
    try:
        np = _DATA / ".hermes/recent_user_need.json"
        if np.exists():
            state["user_expressed_need"] = True
            state["user_need"] = json.loads(np.read_text()).get("need", "")
    except Exception:
        pass
    return state
